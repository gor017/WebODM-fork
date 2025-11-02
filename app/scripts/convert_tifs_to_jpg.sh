#!/bin/bash
# Convert all TIF files to JPEG format for WebODM compatibility

INPUT_DIR="${1:-output_directory}"
OUTPUT_DIR="${2:-output_jpg}"
PATTERN="${3:-*view*.tif}"

mkdir -p "$OUTPUT_DIR"

echo "Converting TIF files to JPEG..."
echo "Input: $INPUT_DIR"
echo "Output: $OUTPUT_DIR"
echo "Pattern: $PATTERN"
echo ""

count=0
for tif_file in "$INPUT_DIR"/$PATTERN; do
    if [ -f "$tif_file" ]; then
        filename=$(basename "$tif_file" .tif)
        jpg_file="$OUTPUT_DIR/${filename}.jpg"
        
        echo "Converting: $(basename $tif_file) -> $(basename $jpg_file)"
        
        if gdal_translate -of JPEG -co QUALITY=95 "$tif_file" "$jpg_file" 2>/dev/null; then
            echo "  ✓ Success"
            ((count++))
        else
            echo "  ✗ Failed"
        fi
    fi
done

echo ""
echo "✓ Converted $count files"
echo "Upload JPEG files from $OUTPUT_DIR to WebODM"

