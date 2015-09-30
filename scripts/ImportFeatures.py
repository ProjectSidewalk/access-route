import json

import psycopg2
FEATURE_TYPE_ID = 2
FILENAME = 'construction.geojson'
conn_string = "host='localhost' dbname='sidewalk' user='postgres' password='sidewalk'"
with open(FILENAME) as data_file:
    data = json.load(data_file)
# Establish connection to database before starting

try:
    conn = psycopg2.connect(conn_string)
except:
    print "I am unable to connect to the database"

cursor = conn.cursor()

for thisfeature in data["features"]:
    coordinates = thisfeature["geometry"]["coordinates"]
    # Query the database to figure out which sidewalk id this feature belongs to based on coordinates
    query = """ SELECT sidewalk_edge_id,ST_Distance(wkb_geometry,'SRID=4326;POINT(%s %s)'::geometry)
                FROM sidewalk_edge
                ORDER BY
                sidewalk_edge.wkb_geometry <->'SRID=4326;POINT(%s %s)'::geometry
                LIMIT 1; """
    inputs = (coordinates[0], coordinates[1], coordinates[0], coordinates[1])
    cursor.execute(query, inputs)
    sidewalk_edge_id = cursor.fetchone()
    # sidewalk_edge_id[0] now contains the id of the sidewalk edge closest to this point
    # now insert the feature into accessibility_feature table
    query = """ INSERT INTO accessibility_feature (feature_geometry, feature_type, lng, lat)
                VALUES (ST_GeomFromText('POINT(%s %s)', 4326), %s, %s, %s) """
    inputs = (coordinates[0], coordinates[1], FEATURE_TYPE_ID, coordinates[0], coordinates[1])
    cursor.execute(query, inputs)
    # Get the id of the newly inserted accessibility feature
    query = "SELECT currval('accessibility_features_feature_id_seq')"
    cursor.execute(query)
    new_feature_id = cursor.fetchone()[0]
    closest_sidewalk_id = sidewalk_edge_id[0]
    # Now insert the paired feature id and sidewalk id into sidewalk_edge_accessibility_feature
    query = """ INSERT INTO sidewalk_edge_accessibility_feature (sidewalk_edge_id, accessibility_feature_id)
                VALUES (%s, %s) """
    inputs = (closest_sidewalk_id, new_feature_id)
    cursor.execute(query, inputs)
conn.commit()

