#!/usr/bin/python
FILENAME = "test.sql"

count = 0
f = open(FILENAME, "r")
new_file = open("result.sql", "w")
src_arr = []
dst_arr = []
for line in f:
	 split = line.split()
	 if ' '.join(split[0:3]) == "INSERT INTO sidewalk_edge":
		  if split[22][:-1] not in src_arr:
				src_arr.append(split[22][:-1])
		  if split[23][:-1] not in src_arr:
				src_arr.append(split[23][:-1])
f.close
f = open(FILENAME, "r")
count = len(src_arr)+1
for line in f:
	 split = line.split()
	 if ' '.join(split[0:3]) == "INSERT INTO sidewalk_edge":
		  split[19] = str(count)+","		  
		  if split[22][:-1] in src_arr:
				split[22] = str(src_arr.index(split[22][:-1]))+","
		  else:
				print split[22]
				src_arr.append(split[22][:-1])
				split[22] = str(count) + ","
				count += 1
		  if split[23][:-1] in src_arr:
				split[23] = str(src_arr.index(split[23][:-1]))+","
		  else:
				print "hel;"
				src_arr.append(split[23][:-1])
				split[23] = str(count) + ","
				count += 1
		  count += 1
	 new_file.write(' '.join(split) + "\n")
f.close()
new_file.close()
	 
