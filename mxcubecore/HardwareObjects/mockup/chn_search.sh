#!/bin/bash

# Check if exactly four arguments (file names) are provided
if [ $# -ne 4 ]; then
    echo "Usage: $0 <file1.py> <file2.py> <file3.py> <file4.py>"
    exit 1
fi

# Loop through all provided file names
for file in "$@"; do
    # Check if the file exists
    if [ ! -f "$file" ]; then
        echo "Error: File '$file' not found."
        continue
    fi

    # Extract lines starting with 'cats' and print them with the filename
    echo "Lines from $file:"
    grep '^self._chn*' "$file" || echo "No lines starting with '_chn' found in $file."
    echo
done