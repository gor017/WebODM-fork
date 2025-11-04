# LAS Conversion API - Usage Guide

## Via Web Interface

1. Go to Dashboard (`/dashboard/`)
2. Click "Convert LAS" button (left side, next to "Add Project")
3. Fill in the form:
   - Select your LAS/LAZ file
   - Set **Mode**: `rgb`
   - Enable **"Create Multiple Viewpoints"** checkbox
   - Set **Number of Images (count)**: `50`
   - Optionally enable **"Convert to JPEG"**
4. Click "Convert"

## Via API (curl) - Direct Call

### Get CSRF Token First

```bash
# Get CSRF token from browser cookies or login session
# You need to be authenticated to WebODM

# Get token from browser DevTools -> Application -> Cookies -> csrftoken
CSRF_TOKEN="your_csrf_token_here"
```

### Convert LAS to Images (with count=50, mode=rgb)

```bash
curl -X POST http://localhost:8000/api/las-convert/ \
  -H "X-CSRFToken: $CSRF_TOKEN" \
  -H "Cookie: csrftoken=$CSRF_TOKEN; sessionid=your_session_id" \
  -F "file=@/Users/harut/Downloads/Объёмы.las" \
  -F "mode=rgb" \
  -F "resolution=0.1" \
  -F "multiview=true" \
  -F "tile_size=100" \
  -F "overlap=0.3" \
  -F "count=50" \
  -F "convert_to_jpg=true"
```

### API Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file` | File | Required | LAS/LAZ file to convert |
| `mode` | string | `intensity` | `rgb`, `intensity`, `elevation`, or `count` |
| `resolution` | float | `0.1` | Output resolution in meters |
| `multiview` | boolean | `false` | Enable multiple viewpoints |
| `tile_size` | float | `100` | Tile size in meters (for multiview) |
| `overlap` | float | `0.3` | Overlap between tiles (0.0-1.0) |
| `count` | int | `30` | Number of images to generate (multiview only) |
| `convert_to_jpg` | boolean | `false` | Convert output to JPEG format |

### Response

```json
{
  "success": true,
  "temp_dir": "las_conv_abc123",
  "files": [
    "Объёмы_rgb_view_001.jpg",
    "Объёмы_rgb_view_002.jpg",
    ...
  ],
  "count": 50,
  "download_url": "/api/las-convert/download/las_conv_abc123/converted_images.zip"
}
```

### Download Results

```bash
# Download the ZIP file
curl -O "http://localhost:8000/api/las-convert/download/las_conv_abc123/converted_images.zip" \
  -H "Cookie: csrftoken=$CSRF_TOKEN; sessionid=your_session_id"
```

## Python Script (Direct) - Alternative

If you prefer to use the script directly (not via API):

```bash
python app/scripts/las_to_images.py \
  /Users/harut/Downloads/Объёмы.las \
  output_directory/ \
  --mode rgb \
  --multiview \
  --count 50 \
  --tile-size 100 \
  --overlap 0.3
```

## Notes

- **Multiview mode**: When `multiview=true`, the `count` parameter controls how many images are generated
- **RGB mode**: Requires RGB dimensions (Red, Green, Blue) in the LAS file
- **JPEG conversion**: Recommended for better compatibility with WebODM
- **CSRF token**: Required for API calls. Get it from your browser session or login API

