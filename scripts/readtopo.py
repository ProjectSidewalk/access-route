from osgeo import gdal
import os
import psycopg2
import logging as log

# ----CUSTOMIZABLE VARIABLES----

CONN_STRING = "dbname='sidewalk' user='postgres' host='localhost' password='sidewalk'"
FILENAME = 'ned19_n39x00_w077x00_md_washingtondc_2008.img'
# This file covers an area of a quarter degree lat. by a quarter degree long.
# Adjust as needed
coverage = 0.25
# Location of top left corner, specified in filename
init_lat = 39
init_long = 77

# -----------------------------


output_to = "database"
geo = gdal.Open(FILENAME)
arr = geo.ReadAsArray()
arr_list = arr.tolist()
# Get width/height of the array
dimension = len(arr[0])


increment = coverage/dimension
output = ""

curr_lat = init_lat
curr_long = init_long
if output_to == "file":
    os.remove("output.txt")
    with open("output.txt", "a") as outfile:
        for idx, row in enumerate(arr_list):
            for idx2, col in enumerate(row):

                outfile.write(str(curr_lat) + "\t" + str(curr_long) + "\t" + str(arr_list[idx][idx2]) + "\n")
                curr_long -= increment
            print("finished row " + str(idx))
            curr_lat -= increment
            curr_long = init_long

elif output_to == "database":
    try:
        conn = psycopg2.connect(CONN_STRING)
        cur = conn.cursor()

        for idx, row in enumerate(arr_list):
            for idx2, col in enumerate(row):
                query = "INSERT INTO elevation (lat, long, elevation) VALUES (%s, %s, %s);"
                data = (float(curr_lat), float(curr_long), float(arr_list[idx][idx2]))
                cur.execute(query, data)
                curr_long -= increment
            print("finished row " + str(idx))
            conn.commit()
            curr_lat -= increment
            curr_long = init_long
    except:
        log.exception("Error: unable to connect to database")

