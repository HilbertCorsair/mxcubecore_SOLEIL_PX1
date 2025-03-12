#!/bin/bash

# Check if required arguments are provided
if [ $# -ne 2 ]; then
    echo "Usage: $0 <directory> <branch_name>"
    echo "Example: $0 ./my_project feature-branch"
    exit 1
fi

TARGET_DIR="$1"
BRANCH_NAME="$2"

# Check if target directory exists
if [ ! -d "$TARGET_DIR" ]; then
    echo "Error: Directory '$TARGET_DIR' does not exist."
    exit 1
fi

# Check if we're in a git repository
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    echo "Error: Not in a git repository."
    exit 1
fi

# Check if branch exists
if ! git show-ref --verify --quiet "refs/heads/$BRANCH_NAME" && \
   ! git show-ref --verify --quiet "refs/remotes/origin/$BRANCH_NAME"; then
    echo "Error: Branch '$BRANCH_NAME' does not exist locally or remotely."
    exit 1
fi

echo "Starting replacement process..."
echo "Target directory: $TARGET_DIR"
echo "Source branch for mockups: $BRANCH_NAME"

# Get list of files in the mockup directory on the specified branch
mockup_files=$(git ls-tree -r --name-only "$BRANCH_NAME" -- $TARGET_DIR/mockup/ 2>/dev/null)

if [ -z "$mockup_files" ]; then
    echo "No mockup files found in branch '$BRANCH_NAME'."
    exit 0
fi

# Counter for replaced files
REPLACED=0
TOTAL=0

# Process each file in the target directory
find "$TARGET_DIR" -type f | while read -r file; do
    # Get the filename without path
    filename=$(basename "$file")
    
    # Skip files that start with "__"
    if [[ "$filename" == __* ]]; then
        echo "Skipping file starting with '__': $filename"
        continue
    fi
    
    # Check if the filename exists in the mockup directory on the specified branch
    if echo "$mockup_files" | grep -q "$TARGET_DIR/mockup/$filename$"; then
        echo "Found matching file in mockup: $TARGET_DIR/mockup/$filename"
              
        # Copy the file content from the branch's mockup directory to the target directory
        if git show "$BRANCH_NAME:$TARGET_DIR/mockup/$filename" > "$TARGET_DIR/$filename"; then
            echo "Replaced: $target_mockup_dir/$filename"
            ((REPLACED++))
        else
            echo "Failed to replace: $target_mockup_dir/$filename"
        fi
    else
        echo "No mockup found for: $filename"
    fi
    
    ((TOTAL++))
done

echo "Process completed."
echo "Files processed: $TOTAL"
echo "Files replaced: $REPLACED"