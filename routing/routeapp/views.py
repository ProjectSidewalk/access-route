from django.shortcuts import render
from django.http import HttpResponse
from django.core.urlresolvers import reverse
from django.db import connection
from opencage.geocoder import OpenCageGeocode
from geojson import Feature, Point, FeatureCollection
import json
import logging
import geojson
import math

import pdb

logger = logging.getLogger(__name__)
# Create your views here.
global_start_lat = 0
global_end_lat = 0
global_start_lng = 0
global_end_lng = 0


def homepage(request):
    default_startaddr = "Enter start address"
    default_endaddr = "Enter end address"
    routegeojson = """{
    "type": "FeatureCollection",
    "features": []
    }"""
    # When first loading the page, center the map on Washington DC
    average_lat = 38.89
    average_lng = -77.044
    defaultzoom = 11
    return render(request, 'routeapp/homepage.html', {'centerlat':average_lat, 'centerlng':average_lng, 'defaultzoom': defaultzoom, 'start_textbox_value': default_startaddr, 'dest_textbox_value': default_endaddr, 'routegeojson': routegeojson,})


def search(request):
    """
    The bulk of the work is performed in this function, which runs once the user enters a start and end address
    and clicks Submit. In a nutshell, here's what it does:
    1. Retrieve the start and end address that the user submitted using HTTP GET
    2. Query the OpenCage Geocoding API to find the coordinates corresponding to the start and end addresses
    3. Find the start segment, the sidewalk edge closest to the start coordinates. Also find the end segment, the
       sidewalk edge closest to the end coordinates
    4. Split the start segment at the start coordinates, creating two shorter edges. Repeat for end segment.
    5. Create a temporary table containing the four shorter edges in addition to the rest of the sidewalk segments
    6. Run a PgRouting query on the temporary table to find the shortest accessible route. The PgRouting query returns
       the route as a Geojson string.
    7. Query for elevation data at various points along the route and generate a Geojson string that contains both
       the route and the elevation data.
    8. Render an HTML page, inserting the generated Geojson into the page.

    """
    # Get the start and end addresses that the user sent
    address = request.GET['inputaddress'].strip()
    dest = request.GET['inputdest'].strip()
    error=""
    startfound = True
    destfound = True
    lat=0
    lng=0
    destlat=0
    destlng=0
    routegeojson = """{
    "type": "FeatureCollection",
    "features": []
    }"""
    # Query OpenCage to get coordinates for this address
    key = '7945028e977fa9593e5b02378cbf0f27'
    geocoder = OpenCageGeocode(key)
    # Make sure neither start address nor end address is blank first
    if (address != '' and dest != ''):
        # Get coordinates for start address
        # Opencage returns the result in json format
        jsonresult = geocoder.geocode(address)
        
        try:
            # Get the start lat/lng coordinates out of the results sent by OpenCage
            resultdict = jsonresult[0]
            lat = resultdict["geometry"]["lat"]
            lng = resultdict["geometry"]["lng"]
        except IndexError:
            # The start address was not found
            startfound = False
        
        # Repeat the process for destination address
        jsonresult = geocoder.geocode(dest)
        try:
            # Get the end lat/lng coordinates out of the results sent by OpenCage
            resultdict = jsonresult[0]
            destlat = resultdict["geometry"]["lat"]
            destlng = resultdict["geometry"]["lng"]
        except IndexError:
            # The destination address was not found
            destfound = False
        # Display appropriate errors if one or both addresses were not found
        if not startfound and destfound:
            error = "Error: The start address you entered could not be found."
        elif startfound and not destfound:
            error = "Error: The end address you entered could not be found."
        elif not startfound and not destfound:
            error = "Error: Neither addresses you entered could be found."
    else:
        # Display error if one or more fields were left blank
        error = "Error: One or more fields were left blank."

    cursor = connection.cursor()
    cursor.execute("SET search_path TO sidewalk,public;")  # Allow the subsequent queries to access tables under the sidewalk schema without a qualifier (i.e., sidewalk.XXX) http://www.postgresql.org/docs/9.2/static/ddl-schemas.html
    # Find the sidewalk edge closest to the start location and store the value in its 'source' column as start_source_id
    cursor.execute("SELECT source FROM sidewalk_edge ORDER BY ST_Distance(ST_GeomFromText('POINT(%s %s)', 4326), geom) ASC LIMIT 1", [lng, lat])
    row = cursor.fetchone()
    start_source_id = row[0]
    # Find the sidewalk edge closest to the end location and store the value in its 'target' column as end_target_id
    cursor.execute("SELECT target FROM sidewalk_edge ORDER BY ST_Distance(ST_GeomFromText('POINT(%s %s)', 4326), geom) ASC LIMIT 1", [destlng, destlat])
    row = cursor.fetchone()
    end_target_id = row[0]
    
    # Find the sidewalk edge closest to the start location and store its 'sidewalk_edge_id' as start_edge_id
    cursor.execute("SELECT sidewalk_edge_id FROM sidewalk_edge ORDER BY ST_Distance(ST_GeomFromText('POINT(%s %s)', 4326), geom) ASC LIMIT 1", [lng, lat])
    row = cursor.fetchone()
    start_edge_id = row[0]
    # Find the sidewalk edge closest to the end location and store its 'sidewalk_edge_id' as end_edge_id
    
    cursor.execute("SELECT sidewalk_edge_id FROM sidewalk_edge ORDER BY ST_Distance(ST_GeomFromText('POINT(%s %s)', 4326), geom) ASC LIMIT 1", [destlng, destlat])
    row = cursor.fetchone()
    end_edge_id = row[0]

    # Find location in the middle of the route for centering the map
    average_lat = (lat + destlat)/2.0
    average_lng = (lng + destlng)/2.0

    # The following gigantic SQL query creates a temporary table called combined_sidewalk_edge which contains
    # four new edges resulting from splitting the start segment at the start coordinates and the end segment at the
    # end coordinates, in addition to all the original sidewalk edges. This is necessary because we need to route
    # from the exact start point to the exact end point, not from the start segment to the end segment.
    cursor.execute("DISCARD TEMP;")
    create_temp_query = """
    CREATE TEMP TABLE combined_sidewalk_edge AS
    SELECT sidewalk_edge_id, geom, target, source  FROM sidewalk_edge
    UNION

    SELECT -1000 as sidewalk_edge_id, (
    ST_Line_Substring(  (SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1),
        (
            SELECT ST_Line_Locate_Point((SELECT geom FROM sidewalk_edge WHERE sidewalk_edge_id = %s LIMIT 1), (
                SELECT ST_ClosestPoint(ST_GeomFromText('POINT(%s %s)', 4326), (SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1) ))
            )
        )
    ,1) 
    ) as geom, (SELECT target FROM sidewalk_edge WHERE sidewalk_edge_id = %s LIMIT 1) as target, '-123' as source

    UNION
    SELECT -1001 as sidewalk_edge_id, (
    ST_Line_Substring(  (SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1),0,
        (
            SELECT ST_Line_Locate_Point((SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1), (
                SELECT ST_ClosestPoint(ST_GeomFromText('POINT(%s %s)', 4326),(SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1) ))
            )
        )
    ) 
    ) as geom, '-123' as target, (SELECT source FROM sidewalk_edge WHERE sidewalk_edge_id=%s LIMIT 1) as source
    
    UNION

    SELECT -1002 as sidewalk_edge_id, (
    ST_Line_Substring(  (SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1),0,
        (
            SELECT ST_Line_Locate_Point((SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1), (
                SELECT ST_ClosestPoint(ST_GeomFromText('POINT(%s %s)', 4326),(SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1) ))
            )
        )
    ) 
    ) as geom, '-124' as target, (SELECT source FROM sidewalk_edge WHERE sidewalk_edge_id=%s LIMIT 1) as source
    
    UNION

    SELECT -1003 as sidewalk_edge_id, (
    ST_Line_Substring(  (SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1),
        (
            SELECT ST_Line_Locate_Point((SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1), (
                SELECT ST_ClosestPoint(ST_GeomFromText('POINT(%s %s)', 4326),(SELECT geom from sidewalk_edge where sidewalk_edge_id = %s LIMIT 1) ))
            )
        )
    ,1) 
    ) as geom, (SELECT target FROM sidewalk_edge WHERE sidewalk_edge_id = %s LIMIT 1) as target, '-124' as source
    ;
    """

    cursor.execute(create_temp_query, [start_edge_id, start_edge_id, lng, lat, start_edge_id, start_edge_id,
                                       start_edge_id, start_edge_id, lng, lat, start_edge_id, start_edge_id,
                                       end_edge_id, end_edge_id, destlng, destlat, end_edge_id, end_edge_id,
                                       end_edge_id, end_edge_id, destlng, destlat, end_edge_id, end_edge_id])
    cursor.execute("select * from combined_sidewalk_edge")

    # Now that the temporary table combined_sidewalk_edge has been created, we can query for a route from the start
    # location to the end location. This query will return a route as a Geojson string.

    # Todo. I changed calculate_accessible_cost to 1 for now.
    routesql = """
     SELECT ST_AsGeoJSON(st_union) FROM (
     SELECT ST_Union(geom) FROM (
     SELECT seq, id1 AS node, id2 AS edge, route.cost, dt.geom, dt.sidewalk_edge_id FROM pgr_dijkstra('
                 SELECT sidewalk_edge_id AS id,
                          source::integer,
                          target::integer,
                          calculate_accessible_cost(sidewalk_edge_id)::double precision AS cost
                         FROM combined_sidewalk_edge',
                 %s, %s, false, false) as route
		 		join combined_sidewalk_edge  dt
		 		on route.id2 = dt.sidewalk_edge_id
     ) as routegeometries
     ) as final; """
    #routesql = """
    #SELECT ST_AsGeoJSON(route_geom) FROM (
    #SELECT ST_Union(geom) as route_geom FROM (
    #    SELECT seq, id1 AS node, id2 AS edge, route.cost, dt.geom, dt.sidewalk_edge_id FROM pgr_dijkstra('SELECT sidewalk_edge_id AS id,
    #            source::int4, target::int4, 1.0::float8 AS cost FROM combined_sidewalk_edge', -123, -124, false, false
    #            ) as route
    #    join combined_sidewalk_edge dt on route.id2 = dt.sidewalk_edge_id
    #) as routegeometries
#) as final;""".replace("\t", "").replace("\n", "")
    # The source will always be -123 and the target will always be -124 because those are the source/target values we
    # assigned to the newly generated edges in the gigantic SQL query above.

    #cursor.execute(routesql)
    cursor.execute(routesql, ["-123", "-124"])
    row = cursor.fetchone()
    # Store the geojson string describing the route as routejs
    routejs = row[0]

    # Unfortunately, the paths that make up the route are not ordered in the geojson returned by PostGIS. Before
    # we can query for elevation, we need to order the paths.

    """
    Path sorting algorithm description:
    - Parse the json from PostGIS into a list named 'data'. The 'data' list is nested: It is a list of paths,
      each path is a list of points, and each point is a list containing lat and lng. So 'data' looks like this:
      [
      [ [5,6][7,8] ]
      [ [9,10][11,12][13,14] ]
      ]
    - Create an empty list 'data_ordered'. It will have the same structure as the 'data' list, but of course
      it will store the paths in order.
    - Remove a path from 'data' and put it into data_ordered
    - Find the lat/lng coordinates of the LAST point in this path
    - Search through 'data' for a path that either begins or ends at the coordinates found in the last step. This is
      the path that goes next, so append it to data_ordered and remove it from 'data'. If needed, reverse the order of
      the points in the newly appended path so that the common coordinates "meet". For instance, if the original path
      is [ [6,7][12,14][3,6] ] and the new path to append is [ [3,8][3,4][3,6] ], here's what data_ordered should look
      like after this step:
      [
      [ [6,7][12,14][3,6] ]
      [ [3,6][3,4][3,8] ]
      ]
    - Keep repeating this until no new path to append is found, at which point we have reached the end of the route.
      Now do this again, but prepending paths that should go before the first path currently in 'data_ordered'
      (rather than appending paths that go after the last one). The process continues until the 'data' list contains
      no more paths.

    - Create a new geojson string from 'data_ordered'
    """
    
    data = json.loads(routejs)
    points = []

    data_ordered = []
    begin_found = False
    end_found = False
    # Add the first path to data_ordered
    data_ordered.append(data['coordinates'].pop())

    while not begin_found or not end_found:
        # If the last segment hasn't been found yet
        if not end_found:
            # Find the path that goes after the last one, and append it to data_ordered
            search_lng = data_ordered[-1][-1][0]
            search_lat = data_ordered[-1][-1][1]
            next_segment_found = False
            for path in data['coordinates']:
                start_point = path[0]
                end_point = path[-1]
                if start_point[0] == search_lng and start_point[1] == search_lat:
                    # We found the path that goes next
                    next_segment_found = True
                    # Append it to data_ordered
                    data_ordered.append(path)
                    # Remove it from data
                    data['coordinates'].remove(path)
                elif end_point[0] == search_lng and end_point[1] == search_lat:
                    # Same as above, but the points in the path are in the opposite order
                    next_segment_found = True
                    data_ordered.append(path[::-1]) # Reverse the order of points in path before appending it
                    data['coordinates'].remove(path)
            # If the path that goes next was not found, we have reached the end of the route.
            if not next_segment_found:
                end_found = True
        # Now repeat this process backward until we reach the beginning of the route
        if not begin_found:
            # Find the path that goes before the first one, and prepend it to data_ordered
            search_lng = data_ordered[0][0][0]
            search_lat = data_ordered[0][0][1]
            previous_segment_found = False
            for path in data['coordinates']:
                start_point = path[0]
                end_point = path[-1]
                if start_point[0] == search_lng and start_point[1] == search_lat:
                    # We've found the path that goes before the first path currently in data_ordered
                    previous_segment_found = True
                    # Prepend the path to data_ordered. Order of the points in the path need to be reversed first.
                    data_ordered.insert(0, path[::-1])
                    # Remove the path from data
                    data['coordinates'].remove(path)
                elif end_point[0] == search_lng and end_point[1] == search_lat:
                    # Same as above but order of the points in the path does not need to be reversed.
                    previous_segment_found = True
                    data_ordered.insert(0, path)
                    data['coordinates'].remove(path)
            if not previous_segment_found:
                begin_found = True

    firstpath = data_ordered[0]
    lastpath = data_ordered[-1]
    route_start_lng = firstpath[0][0]
    route_start_lat = firstpath[0][1]
    route_end_lng = lastpath[-1][0]
    route_end_lat = lastpath[-1][1]

    # Sometimes, the first path in data_ordered will be the end segment and the last one will be the start
    # segment, so the entire data_ordered list may need to be reversed. Check if this is necessary and if so,
    # reverse the order of the paths and the order of the points in each path.

    # Determine if the order of path in data_ordered needs to be reversed
    # Find distance from start point to first point in data_ordered
    start_to_first_dist = math.hypot(lng - route_start_lng, lat - route_start_lat)
    # Now find distance from start point to last point in data_ordered
    start_to_last_dist = math.hypot(lng - route_end_lng, lat - route_end_lat)
    # If the latter is less than the former, data_ordered needs to be reversed
    if start_to_last_dist < start_to_first_dist:
        # Reverse order of the paths
        data_ordered.reverse()
        for path in data_ordered:
            # Also reverse order of the points in each path
            path.reverse()
        firstpath = data_ordered[0]
        lastpath = data_ordered[-1]
        route_start_lng = firstpath[0][0]
        route_start_lat = firstpath[0][1]
        route_end_lng = lastpath[-1][0]
        route_end_lat = lastpath[-1][1]

    # Finally, query for elevation data

    # Make a list of all the points along the route
    for path in data_ordered:
        for point in path:
            points.append(point)

    # Split the route into many shorter segments (so that there are more points to query elevation for)
    split_path = split(points)
    # Get elevation for each point on the split route; store in list
    elevation_list = get_elevations(split_path)
    # Generate a geojson string that contains both the split route and the elevation data
    output_string = output_geojson(split_path, elevation_list)

    routegeojson = routejs
    
    # Get nearby accessibility features to mark on map
    
    
    nearby_feature_sql = """ SELECT * FROM accessibility_feature
    WHERE ST_Distance_Sphere(feature_geometry, ST_MakePoint(%s, %s)) <= 3 * 1609.34 AND feature_type=%s; """
    # Get "construction" features
    cursor.execute(nearby_feature_sql, [lng, lat, 2])
    construction_features = cursor.fetchall()
    construction_points_list = []
    print("construction features")
    for feature in construction_features:
        feature_lng = feature[3]
        feature_lat = feature[4]
        feature_point = Point((feature_lng, feature_lat))
        streetview_img_code = "<h3>Reported construction</h3><a target='_blank' href='http://maps.google.com/?cbll="+str(feature_lat)+","+str(feature_lng)+"&cbp=12,235,,0,5&layer=c'><img src='https://maps.googleapis.com/maps/api/streetview?size=200x200&location="+str(feature_lat)+","+str(feature_lng)+"&fov=90&heading=235&pitch=10' /></a>"
        feature = geojson.Feature(geometry=feature_point, properties={"markertype": "construction","popupContent":streetview_img_code, "markerAddress":address})
        construction_points_list.append(feature)
    construction_collection = geojson.FeatureCollection(construction_points_list, featureid=2)
    construction_geojson = geojson.dumps(construction_collection, sort_keys=True)
    logger.debug(construction_geojson)
    # (Note: Repeat the above code for other kinds of features, if desired.)
    
    # print(routejs)
    return render(request, 'routeapp/homepage.html', {'submitted_start':address, 'submitted_dest':dest,'constructionfeatures':construction_geojson, 'routestartlng':route_start_lng, 'routestartlat':route_start_lat, 'routeendlng':route_end_lng, 'routeendlat':route_end_lat, 'elevationjson':output_string, 'centerlat':average_lat, 'centerlng':average_lng, 'defaultzoom': '17', 'lat':lat, 'lng':lng, 'destlat': destlat, 'destlng':destlng, 'start_textbox_value': address, 'dest_textbox_value': dest, 'error_message':error, 'routegeojson':routegeojson, })


def output_geojson(input_path, input_elevation_list):
    featurelist = []
    # Convert the input path to a python LineString
    path_linestring = geojson.LineString(input_path)
    # Create a feature from path_linestring
    feature = geojson.Feature(geometry=path_linestring, properties={"elevation": input_elevation_list})
    featurelist.append(feature)
    FeatureClct = geojson.FeatureCollection(featurelist)
    # Encode FeatureCollection as JSON
    dump = geojson.dumps(FeatureClct, sort_keys=True)
    return dump


def get_elevations(input_path):
    output_elevations = []
    
    cursor = connection.cursor()
    for point in input_path:
        lon = abs(point[0])
        lat = abs(point[1])
        
        query = """SELECT elevation FROM elevation WHERE lat =
                    (
                    SELECT lat FROM
                    (
                        (SELECT lat,long,elevation FROM elevation WHERE lat >= %s ORDER BY lat LIMIT 1)
                        UNION ALL
                        (SELECT lat,long,elevation FROM elevation WHERE lat < %s ORDER BY lat DESC LIMIT 1)
                    ) as nearestlat ORDER BY abs(%s-lat) LIMIT 1
                    )
                AND long =
                (
                    SELECT long FROM
                    (
                        (SELECT lat,long,elevation FROM elevation WHERE long >= %s ORDER BY long LIMIT 1)
                        UNION ALL
                        (SELECT lat,long,elevation FROM elevation WHERE long < %s ORDER BY long DESC LIMIT 1)
                    ) as nearestlong ORDER BY abs(%s-long) LIMIT 1
                )"""
        
        cursor.execute(query,[lat,lat,lat,lon,lon,lon])
        results = cursor.fetchall()
        for elevation in results:
            output_elevations.append(elevation[0])
    return output_elevations


def split(path_to_split):
    """
    Takes a path, represented as an array of coordinates, and
    split it into smaller segments.
    :param path_to_split:
    :return:
    """
    idx1 = 0
    idx2 = 1
    complete = False
    while not complete:
        complete = True
        while idx2 < len(path_to_split):
            point1 = path_to_split[idx1]
            point2 = path_to_split[idx2]
            x1 = point1[0]
            y1 = point1[1]
            x2 = point2[0]
            y2 = point2[1]
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist > 0.000003:
                complete = False
                new_x = (x1 + x2)/2
                new_y = (y1 + y2)/2
                new_point = [new_x, new_y]
                path_to_split.insert(idx2, new_point)
            idx1 += 1
            idx2 += 1
    return path_to_split
