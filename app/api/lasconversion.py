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
                         multiview=False, tile_size=100, overlap=0.3, count=30, use_perspective=False):
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
    create_perspective_views = getattr(las_module, 'create_perspective_views', None)
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
        if use_perspective:
            # Use perspective views (azimuth/elevation angles)
            import io
            import contextlib
            
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            
            try:
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    success = create_perspective_views(
                        las_file, output_dir, resolution, mode, count
                    )
                stdout_output = stdout_capture.getvalue()
                stderr_output = stderr_capture.getvalue()
                
                if stdout_output:
                    logger.info(f"Perspective view conversion output: {stdout_output}")
                if stderr_output:
                    logger.warning(f"Perspective view conversion errors: {stderr_output}")
                
                # Find output files with view pattern
                patterns = [
                    "*view*.tif",  # Perspective view pattern
                    f"*{mode}*view*.tif",
                    "*.tif"
                ]
                output_files = []
                for pattern in patterns:
                    files = list(output_dir.glob(pattern))
                    if files:
                        output_files = files
                        break
                
                if output_files:
                    return True, [str(f) for f in output_files], None
                elif not success:
                    error_msg = "Failed to create perspective views"
                    if "ERROR:" in stdout_output:
                        error_msg += f": {stdout_output}"
                    return False, [], error_msg
            except Exception as e:
                logger.error(f"Exception during perspective view conversion: {e}", exc_info=True)
                return False, [], f"Exception during perspective view conversion: {str(e)}"
        elif multiview:
            # Capture stdout/stderr to see print() messages
            import io
            import contextlib
            
            # Redirect stdout to capture print() messages
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            
            try:
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    success = create_multiview_images(
                        las_file, output_dir, resolution, mode, tile_size, overlap, count
                    )
                stdout_output = stdout_capture.getvalue()
                stderr_output = stderr_capture.getvalue()
                
                # Log captured output for debugging
                if stdout_output:
                    logger.info(f"Multiview conversion output: {stdout_output}")
                if stderr_output:
                    logger.warning(f"Multiview conversion errors: {stderr_output}")
                
                # Check if files were actually created even if function returned False
                # Pattern matches: {base_name}_{mode}_tile_r{row}_c{col}.tif
                # Try multiple patterns to catch all possibilities
                patterns = [
                    f"*{mode}*tile*.tif",  # For intensity, elevation, count modes
                    "*view*.tif",  # For RGB mode (if using view naming)
                    "*_tile_*.tif",  # Generic tile pattern
                    "*.tif"  # All TIF files as fallback
                ]
                output_files = []
                for pattern in patterns:
                    files = list(output_dir.glob(pattern))
                    if files:
                        output_files = files
                        break
                
                if output_files:
                    # Files were created, return success even if function returned False
                    logger.info(f"Found {len(output_files)} output files despite function return value")
                    return True, [str(f) for f in output_files], None
                elif not success:
                    # No files and function returned False - provide detailed error
                    error_msg = "Failed to create multiview images"
                    if stdout_output:
                        # Extract error messages from output
                        if "ERROR:" in stdout_output:
                            error_msg += f": {stdout_output}"
                        elif "Could not read" in stdout_output:
                            error_msg += ": " + stdout_output.split("ERROR:")[-1].strip()
                    return False, [], error_msg
            except Exception as e:
                # Capture any exceptions during conversion
                logger.error(f"Exception during multiview conversion: {e}", exc_info=True)
                return False, [], f"Exception during multiview conversion: {str(e)}"
        else:
            base_name = las_file.stem
            output_file = output_dir / f"{base_name}_{mode}_{resolution}m.tif"
            success, error = rasterize_pointcloud(las_file, output_file, resolution, mode)
            if not success:
                error_msg = error if error else "Failed to rasterize point cloud"
                logger.error(f"Rasterization failed: {error_msg}")
                return False, [], error_msg
            return True, [str(output_file)], None
        
        # Get list of created files (fallback for multiview)
        if multiview:
            pattern = "*view*.tif" if mode == 'rgb' else f"*{mode}*tile*.tif"
        else:
            pattern = f"*{mode}*.tif"
        
        output_files = list(output_dir.glob(pattern))
        if output_files:
            return True, [str(f) for f in output_files], None
        else:
            return False, [], "No output files were created"
        
    except Exception as e:
        logger.error(f"LAS conversion error: {e}", exc_info=True)
        return False, [], str(e)


def add_gps_exif_to_jpg(jpg_path, lat, lon, alt=None):
    """
    Add GPS EXIF metadata to a JPEG file.
    
    Args:
        jpg_path: Path to JPEG file
        lat: Latitude (decimal degrees)
        lon: Longitude (decimal degrees)
        alt: Altitude in meters (optional)
    """
    try:
        import piexif
        from PIL import Image
        
        # Load existing EXIF or create new
        try:
            exif_dict = piexif.load(str(jpg_path))
        except:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
        
        # Convert decimal degrees to DMS (degrees, minutes, seconds)
        def decimal_to_dms(decimal):
            degrees = int(decimal)
            minutes_float = (decimal - degrees) * 60
            minutes = int(minutes_float)
            seconds = (minutes_float - minutes) * 60
            return ((degrees, 1), (minutes, 1), (int(seconds * 10000), 10000))
        
        # Set GPS metadata
        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: 'N' if lat >= 0 else 'S',
            piexif.GPSIFD.GPSLatitude: decimal_to_dms(abs(lat)),
            piexif.GPSIFD.GPSLongitudeRef: 'E' if lon >= 0 else 'W',
            piexif.GPSIFD.GPSLongitude: decimal_to_dms(abs(lon)),
        }
        
        if alt is not None:
            gps_ifd[piexif.GPSIFD.GPSAltitude] = (int(abs(alt) * 100), 100)
            gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
        
        exif_dict["GPS"] = gps_ifd
        
        # Save with EXIF
        img = Image.open(str(jpg_path))
        exif_bytes = piexif.dump(exif_dict)
        img.save(str(jpg_path), exif=exif_bytes, quality=95)
        return True
    except Exception as e:
        logger.warning(f"Failed to add GPS EXIF to {jpg_path}: {e}")
        return False


def get_geotiff_center_coords(tif_path):
    """
    Extract center coordinates from a GeoTIFF file.
    
    Returns: (lat, lon, alt) or None if georeferencing not available
    """
    try:
        import rasterio
        from rasterio.warp import transform
        
        with rasterio.open(str(tif_path)) as src:
            # Get bounds in source CRS
            bounds = src.bounds
            
            # Calculate center
            center_x = (bounds.left + bounds.right) / 2
            center_y = (bounds.top + bounds.bottom) / 2
            
            # Transform to WGS84 (EPSG:4326) if needed
            if src.crs and src.crs.to_string() != 'EPSG:4326':
                lon, lat = transform(
                    src.crs,
                    'EPSG:4326',
                    [center_x],
                    [center_y]
                )
                lon, lat = lon[0], lat[0]
            else:
                lon, lat = center_x, center_y
            
            # For point cloud-derived images, we should NOT use the Z value from the GeoTIFF
            # as GPS altitude because:
            # 1. The Z might be in a projected coordinate system (like UTM) with very large values
            # 2. The Z represents elevation in the point cloud's native CRS, not WGS84
            # 3. Using incorrect altitude can cause georeferencing issues in WebODM
            # 
            # Instead, we'll skip altitude and let WebODM calculate it from the reconstruction
            # or use a reasonable default if needed
            alt = None
            
            # Only use altitude if we can be reasonably sure it's in WGS84 geodetic height
            # For LAS-derived images, this is almost never the case, so we skip it
            # If needed, WebODM will calculate proper altitude during reconstruction
            
            return (lat, lon, alt)
    except Exception as e:
        logger.warning(f"Failed to extract coordinates from {tif_path}: {e}")
        return None


def convert_tifs_to_jpgs(input_dir, output_dir):
    """
    Convert TIF files to JPEG format with GPS EXIF metadata.
    
    Returns tuple: (success: bool, jpg_files: list, error: str)
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all TIF files (case insensitive)
    tif_files = list(input_dir.glob("*.tif")) + list(input_dir.glob("*.TIF"))
    if len(tif_files) == 0:
        logger.warning(f"No TIF files found in {input_dir} to convert to JPG")
        return False, [], "No TIF files found to convert"
    
    logger.info(f"Found {len(tif_files)} TIF files to convert to JPG in {input_dir}")
    
    gdal_translate = shutil.which('gdal_translate')
    if not gdal_translate:
        return False, [], "gdal_translate not found"
    
    jpg_files = []
    errors = []
    
    for tif_file in tif_files:
        jpg_file = output_dir / f"{tif_file.stem}.jpg"
        try:
            # Check if file is readable first
            if not os.path.exists(tif_file) or os.path.getsize(tif_file) == 0:
                errors.append(f"{tif_file.name}: File is empty or missing")
                continue
            
            # Convert TIF to JPG with proper handling for different data types
            # For single-band (grayscale), convert to RGB
            # For float data, scale to 8-bit
            cmd = []
            
            # Check if it's a single-band file and needs conversion
            try:
                import rasterio
                import numpy as np
                with rasterio.open(str(tif_file)) as src:
                    band_count = src.count
                    dtype = src.dtypes[0]
                    
                    # Build command based on file characteristics
                    cmd = [gdal_translate, "-of", "JPEG", "-co", "QUALITY=95"]
                    
                    if band_count == 1:
                        # Single band - duplicate to 3 bands for RGB
                        # Use -b 1 -b 1 -b 1 to duplicate the band (doesn't require color table)
                        if dtype in ['float32', 'float64']:
                            # Get min/max for scaling using percentiles to avoid outliers
                            data = src.read(1)
                            # Handle nodata
                            if src.nodata is not None:
                                data_valid = data[data != src.nodata]
                            else:
                                data_valid = data[~np.isnan(data)]
                            
                            if len(data_valid) > 0:
                                # Use 2nd and 98th percentile for more robust scaling
                                p2 = float(np.percentile(data_valid, 2))
                                p98 = float(np.percentile(data_valid, 98))
                                abs_min = float(data_valid.min())
                                abs_max = float(data_valid.max())
                                
                                # Use percentiles if they provide better range, otherwise use absolute min/max
                                if p98 > p2 and (p98 - p2) > (abs_max - abs_min) * 0.1:
                                    min_val = p2
                                    max_val = p98
                                else:
                                    min_val = abs_min
                                    max_val = abs_max
                                
                                if max_val > min_val:
                                    # Scale to 0-255, convert to Byte, then duplicate to 3 bands
                                    logger.debug(f"Scaling {tif_file.name}: {min_val:.2f} to {max_val:.2f} (range: {abs_min:.2f} to {abs_max:.2f})")
                                    cmd.extend(["-scale", str(min_val), str(max_val), "0", "255"])
                                    cmd.extend(["-ot", "Byte"])
                                    cmd.extend(["-b", "1", "-b", "1", "-b", "1"])  # Duplicate band to RGB
                                else:
                                    # All same value - use default scaling
                                    logger.warning(f"{tif_file.name}: All values are the same ({min_val:.2f}), using default scaling")
                                    cmd.extend(["-scale", "0", "1", "0", "255"])
                                    cmd.extend(["-ot", "Byte"])
                                    cmd.extend(["-b", "1", "-b", "1", "-b", "1"])
                            else:
                                # No valid data - just convert and duplicate
                                logger.warning(f"{tif_file.name}: No valid data found")
                                cmd.extend(["-ot", "Byte"])
                                cmd.extend(["-b", "1", "-b", "1", "-b", "1"])
                        else:
                            # Integer single band - convert to Byte if needed, then duplicate to RGB
                            if dtype not in ['uint8', 'Byte']:
                                # For integer types, scale to 0-255 range
                                data = src.read(1)
                                if src.nodata is not None:
                                    data_valid = data[data != src.nodata]
                                else:
                                    data_valid = data
                                
                                if len(data_valid) > 0:
                                    min_val = int(data_valid.min())
                                    max_val = int(data_valid.max())
                                    if max_val > min_val and max_val > 255:
                                        # Scale down if needed
                                        logger.debug(f"Scaling integer {tif_file.name}: {min_val} to {max_val}")
                                        cmd.extend(["-scale", str(min_val), str(max_val), "0", "255"])
                                    cmd.extend(["-ot", "Byte"])
                                else:
                                    cmd.extend(["-ot", "Byte"])
                            # Duplicate band to create 3-band RGB
                            cmd.extend(["-b", "1", "-b", "1", "-b", "1"])
                    elif band_count == 3:
                        # Already RGB
                        if dtype in ['float32', 'float64']:
                            # Get overall min/max across all bands for scaling using percentiles
                            all_data = src.read()
                            if src.nodata is not None:
                                data_valid = all_data[all_data != src.nodata]
                            else:
                                data_valid = all_data[~np.isnan(all_data)]
                            
                            if len(data_valid) > 0:
                                # Use percentiles for more robust scaling
                                p2 = float(np.percentile(data_valid, 2))
                                p98 = float(np.percentile(data_valid, 98))
                                abs_min = float(data_valid.min())
                                abs_max = float(data_valid.max())
                                
                                if p98 > p2 and (p98 - p2) > (abs_max - abs_min) * 0.1:
                                    min_val = p2
                                    max_val = p98
                                else:
                                    min_val = abs_min
                                    max_val = abs_max
                                
                                if max_val > min_val:
                                    logger.debug(f"Scaling RGB {tif_file.name}: {min_val:.2f} to {max_val:.2f} (range: {abs_min:.2f} to {abs_max:.2f})")
                                    cmd.extend(["-scale", str(min_val), str(max_val), "0", "255"])
                                else:
                                    logger.warning(f"{tif_file.name}: All RGB values are the same ({min_val:.2f})")
                                    cmd.extend(["-scale", "0", "1", "0", "255"])
                            # Output as Byte
                            cmd.extend(["-ot", "Byte"])
                        else:
                            # Integer RGB - check if needs scaling (16-bit to 8-bit)
                            if dtype in ['uint16', 'uint16_t']:
                                # Check actual value range
                                all_data = src.read()
                                if src.nodata is not None:
                                    data_valid = all_data[all_data != src.nodata]
                                else:
                                    data_valid = all_data
                                
                                if len(data_valid) > 0:
                                    max_val = int(data_valid.max())
                                    min_val = int(data_valid.min())
                                    if max_val > 255:
                                        # 16-bit data, scale to 8-bit
                                        logger.debug(f"Scaling 16-bit RGB {tif_file.name}: {min_val} to {max_val}")
                                        cmd.extend(["-scale", str(min_val), str(max_val), "0", "255"])
                                        cmd.extend(["-ot", "Byte"])
                                    elif max_val <= 255:
                                        # Already 8-bit range, just convert type
                                        logger.debug(f"RGB {tif_file.name} already in 8-bit range ({min_val} to {max_val})")
                                        cmd.extend(["-ot", "Byte"])
                                else:
                                    cmd.extend(["-ot", "Byte"])
                            # Already 8-bit or other integer type, should be fine
                            pass
                    elif band_count > 3:
                        # Use first 3 bands
                        cmd.extend(["-b", "1", "-b", "2", "-b", "3"])
                        if dtype in ['float32', 'float64']:
                            cmd.extend(["-ot", "Byte"])  # Convert to byte
                    
                    cmd.extend([str(tif_file), str(jpg_file)])
            except ImportError:
                # rasterio not available, use simple conversion
                # Duplicate band to RGB (works for single-band files without color table)
                cmd = [
                    gdal_translate,
                    "-of", "JPEG",
                    "-co", "QUALITY=95",
                    "-b", "1", "-b", "1", "-b", "1",  # Duplicate to RGB
                    str(tif_file),
                    str(jpg_file)
                ]
            except Exception as e:
                logger.warning(f"Could not analyze {tif_file.name}: {e}, using simple conversion")
                # Fallback: simple conversion with band duplication
                cmd = [
                    gdal_translate,
                    "-of", "JPEG",
                    "-co", "QUALITY=95",
                    "-b", "1", "-b", "1", "-b", "1",  # Duplicate to RGB
                    str(tif_file),
                    str(jpg_file)
                ]
            
            # Run conversion
            logger.debug(f"Running gdal_translate: {' '.join(cmd)}")
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    check=True,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"gdal_translate failed: {e.stderr if e.stderr else str(e)}"
                logger.error(f"Error converting {tif_file.name}: {error_msg}")
                errors.append(f"{tif_file.name}: {error_msg}")
                continue
            
            # Check for warnings/errors in output
            if result.stderr:
                if "error" in result.stderr.lower():
                    logger.warning(f"gdal_translate errors for {tif_file.name}: {result.stderr}")
                    # Don't fail immediately, check if file was created
                else:
                    logger.debug(f"gdal_translate info for {tif_file.name}: {result.stderr}")
            
            # Verify JPG file was created and is valid
            if not os.path.exists(jpg_file):
                errors.append(f"{tif_file.name}: JPG file was not created")
                continue
                
            file_size = os.path.getsize(jpg_file)
            if file_size == 0:
                errors.append(f"{tif_file.name}: JPG file is empty after conversion")
                continue
            
            # Verify it's a valid JPEG by checking file header
            try:
                with open(jpg_file, 'rb') as f:
                    header = f.read(2)
                    if header != b'\xff\xd8':  # JPEG magic number
                        errors.append(f"{tif_file.name}: JPG file has invalid header (not a valid JPEG)")
                        continue
            except Exception as e:
                errors.append(f"{tif_file.name}: Could not verify JPG file: {e}")
                continue
            
            # SKIP GPS EXIF entirely for LAS-derived images
            # The point cloud coordinate system is unclear and may be in a projected CRS (UTM)
            # Adding GPS EXIF from GeoTIFF coordinates causes coordinate system confusion,
            # leading to extreme Z values (-2315296500) during georeferencing that overflow int32
            # OpenSfM doesn't require GPS for reconstruction - it uses feature matching
            # The spatial diversity (different cropped regions, resolutions, angles) is sufficient
            logger.info(f"Skipping GPS EXIF for LAS-derived image {jpg_file.name} (OpenSfM uses feature matching, not GPS)")
            
            # Verify JPG file path is correct
            jpg_file_path = str(jpg_file.resolve())
            jpg_files.append(jpg_file_path)
            logger.info(f"Successfully converted {tif_file.name} -> {jpg_file.name} ({file_size} bytes)")
            logger.debug(f"JPG file path: {jpg_file_path}")
        except Exception as e:
            error_detail = str(e)
            logger.error(f"Failed to convert {tif_file.name}: {error_detail}")
            errors.append(f"Failed to convert {tif_file.name}: {error_detail}")
    
    if len(jpg_files) == 0:
        error_summary = ', '.join(errors[:5])  # Show first 5 errors
        if len(errors) > 5:
            error_summary += f" ... and {len(errors) - 5} more errors"
        logger.error(f"JPG conversion failed - no files converted. Errors: {error_summary}")
        return False, [], f"No files converted. Errors: {error_summary}"
    
    # Verify all JPG files exist
    existing_jpg_files = [f for f in jpg_files if os.path.exists(f)]
    if len(existing_jpg_files) != len(jpg_files):
        missing = len(jpg_files) - len(existing_jpg_files)
        logger.warning(f"{missing} JPG files don't exist, only {len(existing_jpg_files)} files are valid")
        jpg_files = existing_jpg_files
    
    logger.info(f"Successfully converted {len(jpg_files)} TIF files to JPG format")
    logger.info(f"JPG files: {[os.path.basename(f) for f in jpg_files[:5]]}...")
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
        
        # Get parameters (matching script defaults)
        mode = request.data.get('mode', 'intensity')  # Script default is 'intensity', not 'rgb'
        resolution = float(request.data.get('resolution', 0.1))
        multiview = request.data.get('multiview', 'false').lower() == 'true'
        use_perspective = request.data.get('use_perspective', 'false').lower() == 'true'  # Use perspective views (az/el)
        tile_size = float(request.data.get('tile_size', 100))
        overlap = float(request.data.get('overlap', 0.3))
        count = int(request.data.get('count', 30))
        convert_to_jpg = request.data.get('convert_to_jpg', 'true').lower() == 'true'  # Default to JPG for better compatibility
        
        # Create temporary directories
        temp_dir = tempfile.mkdtemp(dir=settings.MEDIA_TMP, prefix='las_conv_')
        tif_dir = os.path.join(temp_dir, 'tif')
        jpg_dir = os.path.join(temp_dir, 'jpg')
        os.makedirs(tif_dir, exist_ok=True)
        os.makedirs(jpg_dir, exist_ok=True)
        
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
            
            # Convert LAS to images (synchronous)
            success, tif_files, error = convert_las_to_images(
                las_path, tif_dir, resolution, mode, multiview, tile_size, overlap, count, use_perspective
            )
            
            if not success:
                raise exceptions.ValidationError(detail=f"Conversion failed: {error}")
            
            # Ensure tif_files are absolute paths
            tif_files = [str(Path(f).resolve()) if not os.path.isabs(f) else f for f in tif_files]
            
            # Verify files exist
            existing_tif_files = [f for f in tif_files if os.path.exists(f)]
            if len(existing_tif_files) != len(tif_files):
                logger.warning(f"Some TIF files don't exist: {len(existing_tif_files)}/{len(tif_files)} files found")
                tif_files = existing_tif_files
            
            # Convert TIF to JPG if requested
            output_files = tif_files
            logger.info(f"convert_to_jpg parameter: {convert_to_jpg}")
            logger.info(f"Number of TIF files created: {len(tif_files)}")
            logger.info(f"TIF directory: {tif_dir}")
            logger.info(f"JPG directory: {jpg_dir}")
            
            if convert_to_jpg:
                logger.info(f"Converting {len(tif_files)} TIF files to JPG format...")
                logger.info(f"TIF files: {[os.path.basename(f) for f in tif_files[:5]]}...")  # Show first 5
                
                # Check if TIF directory has files
                tif_dir_files = list(Path(tif_dir).glob("*.tif")) + list(Path(tif_dir).glob("*.TIF"))
                logger.info(f"TIF directory contains {len(tif_dir_files)} .tif files")
                
                jpg_success, jpg_files, jpg_error = convert_tifs_to_jpgs(tif_dir, jpg_dir)
                logger.info(f"JPG conversion result: success={jpg_success}, files={len(jpg_files)}, error={jpg_error}")
                
                if jpg_success and len(jpg_files) > 0:
                    logger.info(f"Successfully converted {len(jpg_files)} files to JPG")
                    # Ensure JPG files are absolute paths
                    jpg_files = [str(Path(f).resolve()) if not os.path.isabs(f) else f for f in jpg_files]
                    output_files = jpg_files
                else:
                    error_msg = jpg_error if jpg_error else f"JPG conversion failed: {len(jpg_files)} files converted"
                    logger.error(f"TIF to JPG conversion failed: {error_msg}")
                    # Fall back to TIF files if JPG conversion fails
                    if len(jpg_files) == 0:
                        logger.warning("No JPG files were created, using TIF files instead")
            else:
                logger.info("JPG conversion disabled, keeping TIF files")
            
            # Create a zip file with all images for easier download
            import zipfile
            zip_path = os.path.join(temp_dir, 'converted_images.zip')
            
            # Verify output_files format
            logger.info(f"Creating ZIP file with {len(output_files)} files")
            logger.info(f"convert_to_jpg was: {convert_to_jpg}")
            logger.info(f"Output files type check: {[os.path.splitext(f)[1].lower() for f in output_files[:5]]}")
            logger.info(f"Output files: {[os.path.basename(f) for f in output_files[:5]]}...")
            
            # If JPG conversion was requested but we still have TIF files, check jpg_dir
            if convert_to_jpg:
                # Double-check jpg_dir has files
                jpg_dir_files = list(Path(jpg_dir).glob("*.jpg")) + list(Path(jpg_dir).glob("*.JPG"))
                logger.info(f"JPG directory contains {len(jpg_dir_files)} .jpg files")
                if len(jpg_dir_files) > 0 and len(output_files) == len(tif_files):
                    # JPG files exist but we're using TIF files - switch to JPG
                    logger.warning("JPG files exist but output_files still contains TIF files. Switching to JPG files.")
                    output_files = [str(f.resolve()) for f in jpg_dir_files]
                    logger.info(f"Switched to {len(output_files)} JPG files")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_files:
                    if os.path.exists(file_path):
                        zipf.write(file_path, os.path.basename(file_path))
                        logger.debug(f"Added to ZIP: {os.path.basename(file_path)} ({os.path.splitext(file_path)[1]})")
                    else:
                        logger.warning(f"File does not exist, skipping: {file_path}")
            
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

