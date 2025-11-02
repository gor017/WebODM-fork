# LAS to Images Conversion API

## Overview

API endpoint for converting LAS/LAZ point cloud files to images (TIF/JPEG) that can be uploaded to WebODM.

## Endpoint

**POST** `/api/las-convert/`

## Authentication

Requires authentication (JWT token or session).

## Request

**Content-Type:** `multipart/form-data`

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|---------|---------|-------------|
| `file` | File | Yes | - | LAS or LAZ file to convert |
| `mode` | String | No | `rgb` | Conversion mode: `rgb`, `intensity`, `elevation`, `count` |
| `resolution` | Float | No | `0.1` | Pixel resolution in meters |
| `multiview` | String | No | `false` | Create multiple viewpoint images: `true` or `false` |
| `tile_size` | Float | No | `100` | Tile size in meters (for multiview mode) |
| `overlap` | Float | No | `0.3` | Overlap percentage 0.0-1.0 (for multiview mode) |
| `count` | Integer | No | `30` | Number of images for multiview mode |
| `convert_to_jpg` | String | No | `true` | Convert TIF to JPEG: `true` or `false` |

## Response

### Success Response (200 OK)

```json
{
    "success": true,
    "count": 30,
    "temp_dir": "las_conv_abc123",
    "download_url": "/api/las-convert/download/las_conv_abc123/converted_images.zip",
    "files": [
        "Объёмы_view_000_az0_el85.jpg",
        "Объёмы_view_001_az27_el86.jpg",
        ...
    ],
    "message": "Successfully converted LAS file to 30 images. Download as ZIP or individual files."
}
```

### Error Response (400 Bad Request)

```json
{
    "detail": "Conversion failed: [error message]"
}
```

## Download Files

### Download ZIP

**GET** `/api/las-convert/download/<temp_dir>/converted_images.zip`

### Download Individual File

**GET** `/api/las-convert/download/<temp_dir>/<filename>`

## Example Usage

### cURL

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@input.las" \
  -F "mode=rgb" \
  -F "resolution=0.1" \
  -F "multiview=true" \
  -F "convert_to_jpg=true" \
  http://localhost:8000/api/las-convert/
```

### JavaScript/Fetch

```javascript
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('mode', 'rgb');
formData.append('resolution', 0.1);
formData.append('multiview', 'true');
formData.append('convert_to_jpg', 'true');

const response = await fetch('/api/las-convert/', {
    method: 'POST',
    headers: {
        'X-CSRFToken': csrfToken  // Django CSRF token
    },
    body: formData
});

const result = await response.json();
if (result.success) {
    // Download ZIP
    window.location.href = result.download_url;
}
```

### Python

```python
import requests

url = 'http://localhost:8000/api/las-convert/'
files = {'file': open('input.las', 'rb')}
data = {
    'mode': 'rgb',
    'resolution': 0.1,
    'multiview': 'true',
    'convert_to_jpg': 'true'
}
headers = {'Authorization': 'Bearer YOUR_TOKEN'}

response = requests.post(url, files=files, data=data, headers=headers)
result = response.json()
```

## Frontend Integration

Use the `LASConversionPanel` component:

```jsx
import LASConversionPanel from 'app/components/LASConversionPanel';

// In your component
<LASConversionPanel />
```

## Notes

- Files are stored temporarily in `MEDIA_TMP` directory
- Temporary files are cleaned up automatically after some time
- The endpoint requires PDAL and GDAL to be installed
- Conversion can take several minutes for large files
- Recommended to use `multiview=true` and `convert_to_jpg=true` for best WebODM compatibility

