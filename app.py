import geopandas as gpd
from shapely import wkt
import requests
import zipfile
import os
import shutil
import subprocess
import pydeck as pdk
from typing import Tuple  # <-- Ajouté ici

# Constants (example)
BUILDING_DOWNLOAD_PATH = 'gs://open-buildings-data/v3/{data_type}_s2_level_6_gzip_no_header'

def get_filename_and_region_dataframe(region_border_source: str, region: str,
                                      your_own_wkt_polygon: str) -> Tuple[str, gpd.GeoDataFrame]:
    """Retourne le nom du fichier de sortie et un GeoDataFrame avec une ligne représentant la région."""
    if your_own_wkt_polygon:
        filename = f'open_buildings_v3_{data_type}_your_own_wkt_polygon.{output_format}'
        region_df = gpd.GeoDataFrame(
            geometry=gpd.GeoSeries.from_wkt([your_own_wkt_polygon]),
            crs='EPSG:4326')
        if not isinstance(region_df.iloc[0].geometry,
                          shapely.geometry.polygon.Polygon) and not isinstance(
                              region_df.iloc[0].geometry,
                              shapely.geometry.multipolygon.MultiPolygon):
            raise ValueError("`your_own_wkt_polygon` must be a POLYGON or MULTIPOLYGON.")
        print(f'Preparing your_own_wkt_polygon.')
        return filename, region_df
    
    if not region:
        raise ValueError('Please select a region or set your_own_wkt_polygon.')
    
    # Handle region borders
    if region_border_source == 'Natural Earth (Low Res 110m)':
        url = 'https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip'
        subprocess.run(['wget', '-N', url], check=True)
        region_shapefile_path = os.path.basename(url)
        source_name = 'ne_110m'
    elif region_border_source == 'Natural Earth (High Res 10m)':
        url = 'https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip'
        subprocess.run(['wget', '-N', url], check=True)
        region_shapefile_path = os.path.basename(url)
        source_name = 'ne_10m'
    elif region_border_source == 'World Bank (High Res 10m)':
        url = 'https://datacatalogfiles.worldbank.org/ddh-published/0038272/DR0046659/wb_countries_admin0_10m.zip'
        subprocess.run(['wget', '-N', url], check=True)
        subprocess.run(['unzip', '-o', os.path.basename(url)], check=True)
        region_shapefile_path = 'WB_countries_Admin0_10m'
        source_name = 'wb_10m'

    region_iso_a3 = region.split(' ')[0]
    filename = f'open_buildings_v3_{data_type}_{source_name}_{region_iso_a3}.{output_format}'
    region_df = gpd.read_file(region_shapefile_path).query(f'ISO_A3 == "{region_iso_a3}"').dissolve(by='ISO_A3')[['geometry']]
    print(f'Preparing {region} from {region_border_source}.')
    
    return filename, region_df

# Exemple d'utilisation dans Streamlit
import streamlit as st

st.title("Téléchargement des bâtiments")

option = st.radio("Méthode de sélection", ["Sélectionner un pays", "Entrer un polygone WKT"])

# Charger la carte des pays (GeoDataFrame)
@st.cache_data
def charger_pays():
    return gpd.read_file("countries.geojson")

countries_gdf = charger_pays()

if option == "Sélectionner un pays":
    pays = st.selectbox("Choisissez un pays", sorted(countries_gdf["name"].unique()))
    zone = countries_gdf[countries_gdf["name"] == pays]
else:
    wkt_string = st.text_area("Collez un polygone au format WKT")
    zone = None
    if wkt_string:
        try:
            geom = wkt.loads(wkt_string)
            zone = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
        except Exception as e:
            st.error(f"Erreur dans le WKT : {e}")

# Si une zone est définie
if zone is not None:
    # Centrer la carte sur la zone
    center = zone.geometry.centroid.iloc[0]
    layer = pdk.Layer(
        "GeoJsonLayer",
        data=zone.__geo_interface__,
        get_fill_color="[180, 180, 255, 140]",
        pickable=True,
    )

    view_state = pdk.ViewState(
        latitude=center.y,
        longitude=center.x,
        zoom=6,
        pitch=0
    )

    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))

    if st.button("Télécharger les bâtiments de cette zone"):
        with st.spinner("Téléchargement en cours..."):
            bounds = zone.total_bounds
            minx, miny, maxx, maxy = bounds
            url = f"https://storage.googleapis.com/openbuildingsdownload/v3/polygons_s2_level_13_{miny:.4f}_{minx:.4f}_{maxy:.4f}_{maxx:.4f}.zip"

            try:
                response = requests.get(url)
                if response.status_code == 200:
                    zip_path = "batiments_temp.zip"
                    with open(zip_path, "wb") as f:
                        f.write(response.content)

                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall("batiments_temp")

                    shp_file = next((f for f in os.listdir("batiments_temp") if f.endswith(".shp")), None)
                    if not shp_file:
                        st.error("Aucun fichier .shp trouvé dans l'archive.")
                    else:
                        batiments = gpd.read_file(os.path.join("batiments_temp", shp_file))
                        batiments_zone = batiments[batiments.geometry.within(zone.unary_union)]

                        # Choix de précision
                        precision = st.selectbox("Précision minimale", ["Toutes", "≥ 0.65", "≥ 0.7", "≥ 0.75"])
                        if precision != "Toutes":
                            seuil = float(precision.split("≥ ")[1])
                            batiments_zone = batiments_zone[batiments_zone["confidence"] >= seuil]

                        st.success(f"{len(batiments_zone)} bâtiments trouvés.")

                        # Format d'export
                        format = st.selectbox("Format", ["GeoJSON", "Shapefile"])

                        if format == "GeoJSON":
                            batiments_zone.to_file("batiments_export.geojson", driver="GeoJSON")
                            with open("batiments_export.geojson", "rb") as f:
                                st.download_button("Télécharger (GeoJSON)", f, "batiments.geojson", "application/geo+json")
                        else:
                            batiments_zone.to_file("batiments_export.shp")
                            shutil.make_archive("batiments_export", 'zip', ".", "batiments_export.shp")
                            with open("batiments_export.zip", "rb") as f:
                                st.download_button("Télécharger (Shapefile)", f, "batiments.zip", "application/zip")

                    # Nettoyage
                    shutil.rmtree("batiments_temp", ignore_errors=True)
                    if os.path.exists("batiments_temp.zip"):
                        os.remove("batiments_temp.zip")
                else:
                    st.error("Aucune donnée trouvée pour cette zone.")
            except Exception as e:
                st.error(f"Erreur de téléchargement : {e}")
