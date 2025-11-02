"""
API endpoint for converting LAS/LAZ files to images.
"""
import os
import sys
import subprocess
import json
import tempfile
import shutil
import logging
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, JsonResponse
from rest_framework.views import APIView
from rest_framework import status, exceptions, permissions
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger('app.logger')


def check_pdal():
    """Check if PDAL is installed and available."""
    try:
        subprocess.run(["pdal", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_gdal():
    """Check if GDAL tools are available."""
    gdal_translate = shutil.which('gdal_translate')
    return gdal_translate is not None


def get_las_info(las_file):
    """Get information about the LAS file using PDAL."""
    try:
        result = subprocess.run(
            ["pdal", "info", "--summary", str(las_file)],
            capture_output=True,
            check=True,
            text=True
        )
        return json.loads(result.stdout).get('summary', {})
    except Exception as e:
        logger.error(f"Could not read LAS file info: {e}")
        return None


def convert_las_to_images(las_file, output_dir, resolution=0.1, mode='rgb', 
                         multiview=False, tile_size=100, overlap=0.3, count=30):
    """
    Convert LAS file to images using the conversion script logic.
    
    Returns tuple: (success: bool, output_files: list, error: str)
    """
    # Import conversion functions dynamically
    import importlib.util
    import sys
    
    # Get the script path
    from django.conf import settings
    base_dir = getattr(settings, 'BASE_DIR', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    script_path = os.path.join(base_dir, 'app', 'scripts', 'las_to_images.py')
    
    if not os.path.exists(script_path):
        return False, [], f"Conversion script not found at {script_path}"
    
    spec = importlib.util.spec_from_file_location("las_to_images_module", script_path)
    if spec is None or spec.loader is None:
        return False, [], "Could not load conversion script"
    
    las_module = importlib.util.module_from_spec(spec)
    sys.modules['las_to_images_module'] = las_module
    spec.loader.exec_module(las_module)
    
    create_multiview_images = getattr(las_module, 'create_multiview_images', None)
    rasterize_pointcloud = getattr(las_module, 'rasterize_pointcloud', None)
    
    if not create_multiview_images or not rasterize_pointcloud:
        return False, [], "Required functions not found in conversion script"
    
    las_file = Path(las_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not las_file.exists():
        return False, [], "Input file does not exist"
    
    if not las_file.suffix.lower() in ['.las', '.laz']:
        return False, [], "Input file must be .las or .laz"
    
    try:
        if multiview:
            success = create_multiview_images(
                las_file, output_dir, resolution, mode, tile_size, overlap
            )
            if not success:
                return False, [], "Failed to create multiview images"
        else:
            base_name = las_file.stem
            output_file = output_dir / f"{base_name}_{mode}_{resolution}m.tif"
            success = rasterize_pointcloud(las_file, output_file, resolution, mode)
            if not success:
                return False, [], "Failed to rasterize point cloud"
            return True, [str(output_file)], None
        
        # Get list of created files
        if multiview:
            pattern = "*view*.tif" if mode == 'rgb' else f"*{mode}*tile*.tif"
        else:
            pattern = f"*{mode}*.tif"
        
        output_files = list(output_dir.glob(pattern))
        return True, [str(f) for f in output_files], None
        
    except Exception as e:
        logger.error(f"LAS conversion error: {e}")
        return False, [], str(e)


def convert_tifs_to_jpgs(input_dir, output_dir):
    """
    Convert TIF files to JPEG format.
    
    Returns tuple: (success: bool, jpg_files: list, error: str)
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all TIF files
    tif_files = list(input_dir.glob("*.tif"))
    if len(tif_files) == 0:
        return False, [], "No TIF files found to convert"
    
    gdal_translate = shutil.which('gdal_translate')
    if not gdal_translate:
        return False, [], "gdal_translate not found"
    
    jpg_files = []
    errors = []
    
    for tif_file in tif_files:
        jpg_file = output_dir / f"{tif_file.stem}.jpg"
        try:
            subprocess.run(
                [gdal_translate, "-of", "JPEG", "-co", "QUALITY=95", 
                 str(tif_file), str(jpg_file)],
                capture_output=True,
                check=True
            )
            jpg_files.append(str(jpg_file))
        except Exception as e:
            errors.append(f"Failed to convert {tif_file.name}: {str(e)}")
    
    if len(jpg_files) == 0:
        return False, [], f"No files converted. Errors: {', '.join(errors)}"
    
    return True, jpg_files, None


class LASConversionView(APIView):
    """
    API endpoint to convert LAS/LAZ files to images.
    
    POST /api/las-convert/
    - Multipart form data with 'file' (LAS/LAZ file)
    - Optional parameters:
      - mode: 'rgb', 'intensity', 'elevation', 'count' (default: 'rgb')
      - resolution: pixel resolution in meters (default: 0.1)
      - multiview: 'true' or 'false' (default: 'false')
      - tile_size: tile size in meters for multiview (default: 100)
      - overlap: overlap percentage 0.0-1.0 (default: 0.3)
      - count: number of images for multiview (default: 30)
      - convert_to_jpg: 'true' or 'false' (default: 'true')
    """
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = (permissions.IsAuthenticated,)
    
    def post(self, request):
        # Check prerequisites
        if not check_pdal():
            raise exceptions.ValidationError(
                detail="PDAL is not installed or not in PATH. Please install PDAL."
            )
        
        # Get uploaded file
        if 'file' not in request.FILES:
            raise exceptions.ValidationError(detail="No file uploaded")
        
        las_file = request.FILES['file']
        
        # Validate file extension
        filename = las_file.name
        if not filename.lower().endswith(('.las', '.laz')):
            raise exceptions.ValidationError(
                detail="File must be .las or .laz format"
            )
        
        # Get parameters
        mode = request.data.get('mode', 'rgb')
        resolution = float(request.data.get('resolution', 0.1))
        multiview = request.data.get('multiview', 'false').lower() == 'true'
        tile_size = float(request.data.get('tile_size', 100))
        overlap = float(request.data.get('overlap', 0.3))
        count = int(request.data.get('count', 30))
        convert_to_jpg = request.data.get('convert_to_jpg', 'true').lower() == 'true'
        
        # Create temporary directories
        temp_dir = tempfile.mkdtemp(dir=settings.MEDIA_TMP, prefix='las_conv_')
        tif_dir = os.path.join(temp_dir, 'tif')
        jpg_dir = os.path.join(temp_dir, 'jpg')
        
        try:
            # Save uploaded file
            las_path = os.path.join(temp_dir, filename)
            
            # Handle different file upload types
            # WebODM uses ClosedTemporaryUploadedFile which closes the file after upload
            # to save file descriptors. We need to use temporary_file_path() for these.
            from django.core.files.uploadedfile import InMemoryUploadedFile
            
            with open(las_path, 'wb') as destination:
                if isinstance(las_file, InMemoryUploadedFile):
                    # In-memory files: use chunks() method
                    for chunk in las_file.chunks():
                        destination.write(chunk)
                elif hasattr(las_file, 'temporary_file_path'):
                    # Temporary files (including ClosedTemporaryUploadedFile):
                    # Copy from temporary file path
                    with open(las_file.temporary_file_path(), 'rb') as source:
                        shutil.copyfileobj(source, destination)
                else:
                    # Fallback: try chunks() method
                    try:
                        for chunk in las_file.chunks():
                            destination.write(chunk)
                    except (ValueError, OSError, IOError, AttributeError) as e:
                        logger.error(f"Failed to read uploaded file: {e}")
                        raise exceptions.ValidationError(
                            detail=f"Failed to read uploaded file: {str(e)}"
                        )
            
            # Convert LAS to images
            success, tif_files, error = convert_las_to_images(
                las_path, tif_dir, resolution, mode, multiview, tile_size, overlap, count
            )
            
            if not success:
                raise exceptions.ValidationError(detail=f"Conversion failed: {error}")
            
            # Convert TIF to JPG if requested
            output_files = tif_files
            if convert_to_jpg:
                jpg_success, jpg_files, jpg_error = convert_tifs_to_jpgs(tif_dir, jpg_dir)
                if jpg_success:
                    output_files = jpg_files
                else:
                    logger.warning(f"TIF to JPG conversion had issues: {jpg_error}")
                    # Fall back to TIF files if JPG conversion fails
            
            # Create a zip file with all images for easier download
            import zipfile
            zip_path = os.path.join(temp_dir, 'converted_images.zip')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_files:
                    if os.path.exists(file_path):
                        zipf.write(file_path, os.path.basename(file_path))
            
            # Create file URLs
            base_url = request.build_absolute_uri('/')[:-1]
            temp_dir_name = os.path.basename(temp_dir)
            
            return Response({
                'success': True,
                'count': len(output_files),
                'temp_dir': temp_dir_name,
                'download_url': f"{base_url}/api/las-convert/download/{temp_dir_name}/converted_images.zip",
                'files': [os.path.basename(f) for f in output_files],
                'message': f'Successfully converted LAS file to {len(output_files)} images. Download as ZIP or individual files.'
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"LAS conversion API error: {e}")
            # Cleanup on error
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise exceptions.ValidationError(detail=f"Conversion failed: {str(e)}")


class LASConversionDownloadView(APIView):
    """
    Download converted images.
    
    GET /api/las-convert/download/<temp_dir>/<filename>
    """
    permission_classes = (permissions.IsAuthenticated,)
    
    def get(self, request, temp_dir, filename):
        # Security: ensure temp_dir and filename don't contain path traversal
        temp_dir = os.path.basename(temp_dir)
        filename = os.path.basename(filename)
        
        # Construct file path - check multiple locations
        possible_paths = [
            os.path.join(settings.MEDIA_TMP, temp_dir, 'converted_images.zip'),
            os.path.join(settings.MEDIA_TMP, temp_dir, 'jpg', filename),
            os.path.join(settings.MEDIA_TMP, temp_dir, 'tif', filename),
            os.path.join(settings.MEDIA_TMP, temp_dir, filename),
        ]
        
        file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                file_path = path
                break
        
        if not file_path or not os.path.exists(file_path):
            raise exceptions.NotFound(detail="File not found")
        
        # Check if it's a zip file or individual file
        # Use FileResponse which properly handles file opening/closing
        file_handle = open(file_path, 'rb')
        response = FileResponse(file_handle)
        if filename.endswith('.zip'):
            response['Content-Type'] = 'application/zip'
        elif filename.endswith('.jpg') or filename.endswith('.jpeg'):
            response['Content-Type'] = 'image/jpeg'
        elif filename.endswith('.tif') or filename.endswith('.tiff'):
            response['Content-Type'] = 'image/tiff'
        
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

