# Troubleshooting Black Images

If all your converted images are black, try these steps:

## Step 1: Check Your LAS File

First, check what dimensions your LAS file has:

```bash
pdal info --summary your_file.las
```

This will show:
- Point count
- Available dimensions (Intensity, Red, Green, Blue, Z, etc.)
- Bounds

## Step 2: Try Single Image First

Before using perspective views, test with a **single image** to verify the basic conversion works:

1. In the frontend, **uncheck** "Use Perspective Views"
2. **Uncheck** "Create Overlapping Tiles"
3. Set mode to **"Elevation"** (this should always work if your file has Z coordinates)
4. Click Convert

If elevation mode works, your pipeline is fine. If it's still black, the issue is with the file or PDAL setup.

## Step 3: Check Resolution

The resolution might be too large or too small:

- **Too large**: If resolution > point cloud size / 10, you'll get very few pixels
- **Too small**: If resolution < point density, you'll get gaps

Try:
- Resolution = 0.1 (default)
- Or auto-calculate: `resolution = sqrt(point_cloud_area / point_count)`

## Step 4: Try Different Modes

Test each mode to see which works:

1. **Elevation** - Should work if file has Z coordinates (most LAS files do)
2. **Intensity** - Only works if file has Intensity values
3. **RGB** - Only works if file has Red, Green, Blue color values
4. **Count** - Always works (shows point density)

## Step 5: Check PDAL Output

The script now prints detailed information:
- Point cloud info
- File sizes
- Data value ranges (min, max, mean)

Look for warnings like:
- `"⚠ Warning: View X is all zeros (black)"`
- `"Available dimensions: [...]"`

## Step 6: Manual Test

Test the PDAL pipeline directly:

```bash
# Create a simple pipeline JSON
cat > test_pipeline.json << EOF
{
  "pipeline": [
    {
      "type": "readers.las",
      "filename": "your_file.las"
    },
    {
      "type": "writers.gdal",
      "filename": "test_output.tif",
      "resolution": 0.1,
      "radius": 0.1,
      "output_type": "mean",
      "dimension": "Z",
      "gdalopts": "COMPRESS=DEFLATE"
    }
  ]
}
EOF

# Run it
pdal pipeline test_pipeline.json

# Check output
gdalinfo test_output.tif
```

## Common Issues

### Issue 1: No Intensity Data
**Symptom**: Intensity mode produces black images
**Solution**: Use Elevation or Count mode instead

### Issue 2: Wrong Resolution
**Symptom**: Images are created but all black
**Solution**: Try different resolution values (0.01, 0.1, 1.0)

### Issue 3: Coordinate System Issues
**Symptom**: Files created but empty
**Solution**: Check if LAS file has proper georeferencing

### Issue 4: Perspective Views All Look Same
**Symptom**: All perspective views are identical (black)
**Cause**: PDAL only creates orthographic views, not true perspective
**Solution**: This is expected - all views will be top-down orthographic

## Quick Fix: Use Elevation Mode

Elevation mode should work with most LAS files:

1. Mode: **Elevation**
2. Resolution: **0.1** (or auto-calculate)
3. **Uncheck** "Use Perspective Views" (test single image first)
4. Click Convert

If elevation mode works, then your file doesn't have Intensity/RGB data, and you should use Elevation mode instead.

