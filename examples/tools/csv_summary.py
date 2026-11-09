"""An intentionally small reusable input-scope tool."""
import argparse
import csv
import json
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('file')
args = p.parse_args()
try:
    with open(args.file, encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader, None)
        if not header or any(not x.strip() for x in header) or len(set(header)) != len(header):
            sys.exit('CSV needs a nonempty, unique header.')
        count, blank = 0, 0
        for row in reader:
            if not row:
                blank += 1
                continue
            if len(row) != len(header):
                sys.exit('CSV row width differs from header; values suppressed.')
            count += 1
    print(json.dumps({'columns': header, 'rows': count, 'blank_rows': blank}))
except (OSError, UnicodeError, csv.Error):
    sys.exit('Cannot read a valid UTF-8 CSV; values suppressed.')
