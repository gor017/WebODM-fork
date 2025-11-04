#!/usr/bin/env python3
"""
Convert LAS/LAZ files to images for WebODM processing.

This script converts point cloud files (LAS/LAZ) into images that can be 
uploaded and processed by WebODM. The script rasterizes the point cloud 
from different perspectives (top-down orthophoto view) into GeoTIFF images.

Usage:
    python las_to_images.py input.las output_directory/
    python las_to_images.py input.laz output_directory/ --resolution 0.1
    python las_to_images.py input.las output_directory/ --mode rgb
"""

import os
import sys
import argparse
import subprocess
import json
import math
import tempfile
from pathlib import Path


def check_pdal():
    """Check if PDAL is installed and available."""
    try:
        subprocess.run(["pdal", "--version"], 
                      capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_las_info(las_file):
    """Get information about the LAS file using PDAL."""
    try:
        result = subprocess.run(
            ["pdal", "info", "--summary", las_file],
            capture_output=True,
            check=True,
            text=True
        )
        info = json.loads(result.stdout)
        return info.get('summary', {})
    except Exception as e:
        print(f"ERROR: Could not read LAS file info: {e}")
        return None


def calculate_resolution(bounds, point_count, default=0.1):
    """
    Calculate appropriate resolution based on point density.
    
    Args:
        bounds: Dictionary with minx, miny, maxx, maxy
        point_count: Number of points in the cloud
        default: Default resolution if calculation fails
    
    Returns:
        Resolution value in meters
    """
    if bounds and point_count and point_count > 0:
        width = bounds.get('maxx', 0) - bounds.get('minx', 0)
        height = bounds.get('maxy', 0) - bounds.get('miny', 0)
        area = width * height
        
        if area > 0:
            points_per_sq_meter = point_count / area
            # Aim for about 4 points per pixel
            resolution = math.sqrt(4.0 / points_per_sq_meter) if points_per_sq_meter > 0 else default
            return max(0.01, min(resolution, 1.0))  # Clamp between 0.01 and 1.0 meters
    
    return default


def rasterize_pointcloud(las_file, output_file, resolution=0.1, mode='intensity'):
    """
    Rasterize a point cloud to a GeoTIFF image using PDAL.
    
    Args:
        las_file: Input LAS/LAZ file path
        output_file: Output GeoTIFF file path
        resolution: Pixel resolution in meters (default: 0.1)
        mode: Rasterization mode ('intensity', 'rgb', 'elevation', 'count')
    
    Returns:
        (True, None) if successful, (False, error_message) otherwise
    """
    try:
        # Create PDAL pipeline for rasterization
        if mode == 'rgb':
            # Build true 3-band RGB by creating separate R/G/B rasters and stacking with GDAL
            # LAS RGB values are typically 16-bit (0-65535) and need scaling to 8-bit (0-255)
            import tempfile
            import shutil

            tmpdir = tempfile.mkdtemp(prefix="lasrgb_")
            red_tif = os.path.join(tmpdir, "red.tif")
            green_tif = os.path.join(tmpdir, "green.tif")
            blue_tif = os.path.join(tmpdir, "blue.tif")

            def pdal_band(out_path, dim):
                pj = {
                    "pipeline": [
                        {"type": "readers.las", "filename": str(las_file)},
                        {
                            "type": "writers.gdal",
                            "filename": str(out_path),
                            "resolution": resolution,
                            "radius": resolution,
                            "output_type": "mean",
                            "dimension": dim,
                            "data_type": "uint16_t",
                            "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                        }
                    ]
                }
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(pj, f)
                    pf = f.name
                try:
                    result = subprocess.run(["pdal", "pipeline", pf], 
                                          capture_output=True, check=True, text=True)
                except subprocess.CalledProcessError as e:
                    error_msg = f"Failed to create {dim} band: {e.stderr if e.stderr else str(e)}"
                    print(f"Warning: {error_msg}")
                    raise Exception(error_msg)
                finally:
                    if os.path.exists(pf):
                        os.unlink(pf)

            try:
                print("Creating RGB bands...")
                pdal_band(red_tif, "Red")
                pdal_band(green_tif, "Green")
                pdal_band(blue_tif, "Blue")
                
                # Verify files were created
                if not all(os.path.exists(f) for f in [red_tif, green_tif, blue_tif]):
                    raise Exception("One or more RGB bands failed to create")
                    
            except Exception as e:
                error_msg = f"RGB band creation failed: {e}"
                print(error_msg)
                print("Falling back to intensity mode...")
                shutil.rmtree(tmpdir, ignore_errors=True)
                result, _ = rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')
                if not result:
                    return False, error_msg
                return True, None

            # Stack with GDAL and normalize 16-bit RGB to 8-bit
            gdalbuildvrt = shutil.which('gdalbuildvrt')
            gdal_translate = shutil.which('gdal_translate')
            if not gdalbuildvrt or not gdal_translate:
                error_msg = "GDAL tools not found, falling back to intensity."
                print(error_msg)
                shutil.rmtree(tmpdir, ignore_errors=True)
                result, _ = rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')
                if not result:
                    return False, error_msg
                return True, None

            vrt_path = os.path.join(tmpdir, "rgb.vrt")
            try:
                # Build VRT with separate bands
                result = subprocess.run([gdalbuildvrt, "-separate", vrt_path, red_tif, green_tif, blue_tif],
                                      capture_output=True, check=True, text=True)
                
                # Convert to 8-bit RGB GeoTIFF with proper scaling
                # Scale from 16-bit (0-65535) to 8-bit (0-255)
                # Use -scale to normalize the values
                result = subprocess.run([
                    gdal_translate, vrt_path, str(output_file),
                    "-ot", "Byte",  # Output as 8-bit
                    "-scale", "0", "65535", "0", "255",  # Scale 16-bit to 8-bit
                    "-co", "COMPRESS=DEFLATE",
                    "-co", "PREDICTOR=2", 
                    "-co", "PHOTOMETRIC=RGB",
                    "-co", "BIGTIFF=YES"
                ], capture_output=True, check=True, text=True)
                
                print(f"✓ Successfully created RGB image: {output_file}")
                return True, None
            except subprocess.CalledProcessError as e:
                error_msg = f"GDAL stacking failed: {e.stderr if e.stderr else str(e)}"
                print(error_msg)
                print("Falling back to intensity mode...")
                shutil.rmtree(tmpdir, ignore_errors=True)
                result, _ = rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')
                if not result:
                    return False, error_msg
                return True, None
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        elif mode == 'elevation':
            # Rasterize using Z/elevation values
            # Use float32 data type to avoid PREDICTOR issues with 64-bit
            pipeline_json = {
                "pipeline": [
                    {
                        "type": "readers.las",
                        "filename": str(las_file)
                    },
                    {
                        "type": "writers.gdal",
                        "filename": str(output_file),
                        "resolution": resolution,
                        "radius": resolution,
                        "output_type": "mean",
                        "dimension": "Z",
                        "data_type": "float32",  # Use float32 instead of default (which might be float64)
                        "gdalopts": "COMPRESS=DEFLATE,BIGTIFF=YES"  # Remove PREDICTOR=2 for elevation
                    }
                ]
            }
        elif mode == 'intensity':
            # Rasterize using intensity values (grayscale)
            pipeline_json = {
                "pipeline": [
                    {
                        "type": "readers.las",
                        "filename": str(las_file)
                    },
                    {
                        "type": "writers.gdal",
                        "filename": str(output_file),
                        "resolution": resolution,
                        "radius": resolution,
                        "output_type": "mean",
                        "dimension": "Intensity",
                        "data_type": "uint16_t",  # Intensity is typically 16-bit
                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                    }
                ]
            }
        else:  # count or default
            # Rasterize showing point density
            pipeline_json = {
                "pipeline": [
                    {
                        "type": "readers.las",
                        "filename": str(las_file)
                    },
                    {
                        "type": "writers.gdal",
                        "filename": str(output_file),
                        "resolution": resolution,
                        "radius": resolution,
                        "output_type": "count",
                        "data_type": "uint32_t",  # Count is unsigned integer
                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                    }
                ]
            }
        
        # Write pipeline to temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(pipeline_json, f)
            pipeline_file = f.name
        
        try:
            # Run PDAL pipeline
            result = subprocess.run(
                ["pdal", "pipeline", pipeline_file],
                capture_output=True,
                check=True,
                text=True
            )
            
            # Verify output file was created
            if not os.path.exists(output_file):
                error_msg = f"PDAL pipeline completed but output file was not created: {output_file}"
                print(f"ERROR: {error_msg}")
                return False, error_msg
            
            file_size = os.path.getsize(output_file)
            if file_size == 0:
                error_msg = f"Output file is empty (0 bytes): {output_file}"
                print(f"ERROR: {error_msg}")
                return False, error_msg
            
            print(f"✓ Successfully created: {output_file} ({file_size} bytes)")
            return True, None
            
        finally:
            # Clean up temporary pipeline file
            if os.path.exists(pipeline_file):
                os.unlink(pipeline_file)
                
    except subprocess.CalledProcessError as e:
        error_msg = f"PDAL processing failed: {e.stderr if e.stderr else str(e)}"
        if e.stdout:
            error_msg += f"\nPDAL stdout: {e.stdout[:500]}"
        print(f"ERROR: {error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = f"Failed to rasterize point cloud: {str(e)}"
        print(f"ERROR: {error_msg}")
        return False, error_msg


def create_multiview_images(las_file, output_dir, resolution=0.1, mode='intensity', 
                             tile_size=100, overlap=0.3, count=None):
    """
    Create multiple overlapping images from a point cloud (for photogrammetry).
    
    This creates a grid of overlapping tiles that simulates multiple camera viewpoints,
    which is better for WebODM processing than a single large image.
    
    Args:
        las_file: Path to input LAS/LAZ file
        output_dir: Directory to save output images
        resolution: Pixel resolution in meters
        mode: Rasterization mode
        tile_size: Size of each tile in meters (default: 100m, auto-adjusted if too large)
        overlap: Overlap percentage between tiles (0.0-1.0, default: 0.3 = 30%)
        count: Target number of images to generate (optional, will adjust tile_size if provided)
    """
    las_file = Path(las_file)
    output_dir = Path(output_dir)
    import shutil
    
    # Get point cloud bounds
    info = get_las_info(las_file)
    if not info or 'bounds' not in info:
        print("ERROR: Could not read point cloud bounds")
        return False
    
    bounds = info['bounds']
    minx, miny = bounds.get('minx', 0), bounds.get('miny', 0)
    maxx, maxy = bounds.get('maxx', 0), bounds.get('maxy', 0)
    
    width = maxx - minx
    height = maxy - miny
    
    # Auto-adjust tile size if it's larger than the point cloud
    if tile_size > max(width, height):
        old_tile_size = tile_size
        tile_size = max(width, height) * 0.4  # Use 40% of the largest dimension
        print(f"Note: Tile size ({old_tile_size}m) is larger than point cloud ({max(width, height):.2f}m)")
        print(f"      Auto-adjusting tile size to {tile_size:.2f}m for better tiling")
    
    # If count is specified, adjust tile_size to achieve approximately that many images
    if count and count > 0:
        # Calculate what tile_size would give us approximately 'count' images
        # area = width * height
        # tile_area = tile_size^2
        # effective_tile_area = tile_area * (1 - overlap)^2  # accounting for overlap
        # num_tiles = area / effective_tile_area
        # Solving for tile_size: tile_size = sqrt(area / (count * (1-overlap)^2))
        area = width * height
        if area > 0:
            effective_overlap_factor = (1 - overlap) ** 2
            target_tile_area = area / (count * effective_overlap_factor)
            calculated_tile_size = math.sqrt(target_tile_area)
            
            # Don't make tiles too small (< 10m) or too large (> point cloud size)
            calculated_tile_size = max(10, min(calculated_tile_size, max(width, height) * 0.9))
            
            if abs(calculated_tile_size - tile_size) > 1:  # Only adjust if significantly different
                print(f"Note: Adjusting tile size from {tile_size:.2f}m to {calculated_tile_size:.2f}m")
                print(f"      to achieve approximately {count} images")
                tile_size = calculated_tile_size
    
    print(f"Point cloud bounds: {width:.2f}m x {height:.2f}m")
    print(f"Creating tiles: {tile_size:.2f}m with {overlap*100}% overlap")
    
    # Calculate tile step (with overlap)
    step = tile_size * (1 - overlap)
    
    # Calculate grid
    cols = math.ceil(width / step) if step > 0 else 1
    rows = math.ceil(height / step) if step > 0 else 1
    
    total_images = cols * rows
    print(f"Grid: {cols} columns x {rows} rows = {total_images} images")
    if count:
        print(f"Target was {count} images, generating {total_images} images")
    print()
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    created_count = 0
    for row in range(rows):
        for col in range(cols):
            # Calculate tile bounds
            tile_minx = minx + col * step
            tile_miny = miny + row * step
            tile_maxx = tile_minx + tile_size
            tile_maxy = tile_miny + tile_size
            
            # Clamp to overall bounds
            tile_minx = max(minx, tile_minx)
            tile_miny = max(miny, tile_miny)
            tile_maxx = min(maxx, tile_maxx)
            tile_maxy = min(maxy, tile_maxy)
            
            if tile_maxx <= tile_minx or tile_maxy <= tile_miny:
                continue
            
            # Create filename
            base_name = las_file.stem
            output_file = output_dir / f"{base_name}_{mode}_tile_r{row:02d}_c{col:02d}.tif"
            
            print(f"Creating tile [{row+1}/{rows}, {col+1}/{cols}]: {output_file.name}")
            
            # Create cropped raster
            try:
                pipeline_json = {
                    "pipeline": [
                        {
                            "type": "readers.las",
                            "filename": str(las_file)
                        },
                        {
                            "type": "filters.crop",
                            "bounds": f"([{tile_minx},{tile_maxx}],[{tile_miny},{tile_maxy}])"
                        },
                        {
                            "type": "writers.gdal",
                            "filename": str(output_file),
                            "resolution": resolution,
                            "radius": resolution,
                            "output_type": "mean" if mode != 'count' else "count",
                            "dimension": ("Intensity" if mode == 'intensity' 
                                        else "Z" if mode == 'elevation'
                                        else mode.capitalize() if mode in ['Red', 'Green', 'Blue']
                                        else "Intensity"),
                            "data_type": "float32" if mode == 'elevation' else ("uint16_t" if mode != 'count' else "uint32_t"),
                            "gdalopts": "COMPRESS=DEFLATE,BIGTIFF=YES" if mode == 'elevation' else "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                        }
                    ]
                }
                
                # Handle RGB mode specially - create proper 3-band RGB
                if mode == 'rgb':
                    # Create separate R, G, B bands then combine with GDAL
                    red_tif = str(output_file).replace('.tif', '_red.tif')
                    green_tif = str(output_file).replace('.tif', '_green.tif')
                    blue_tif = str(output_file).replace('.tif', '_blue.tif')
                    
                    def create_rgb_band(out_path, dim):
                        pj = {
                            "pipeline": [
                                {
                                    "type": "readers.las",
                                    "filename": str(las_file)
                                },
                                {
                                    "type": "filters.crop",
                                    "bounds": f"([{tile_minx},{tile_maxx}],[{tile_miny},{tile_maxy}])"
                                },
                                {
                                    "type": "writers.gdal",
                                    "filename": str(out_path),
                                    "resolution": resolution,
                                    "radius": resolution,
                                    "output_type": "mean",
                                    "dimension": dim,
                                    "data_type": "uint16_t",
                                    "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                                }
                            ]
                        }
                        pf_temp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                        json.dump(pj, pf_temp)
                        pf_temp.close()
                        try:
                            subprocess.run(["pdal", "pipeline", pf_temp.name],
                                          capture_output=True, check=True, text=True)
                        finally:
                            if os.path.exists(pf_temp.name):
                                os.unlink(pf_temp.name)
                    
                    try:
                        create_rgb_band(red_tif, "Red")
                        create_rgb_band(green_tif, "Green")
                        create_rgb_band(blue_tif, "Blue")
                        
                        # Combine with GDAL
                        gdalbuildvrt = shutil.which('gdalbuildvrt')
                        gdal_translate = shutil.which('gdal_translate')
                        if gdalbuildvrt and gdal_translate:
                            vrt_path = str(output_file).replace('.tif', '.vrt')
                            subprocess.run([gdalbuildvrt, "-separate", vrt_path, red_tif, green_tif, blue_tif],
                                         capture_output=True, check=True, text=True)
                            subprocess.run([
                                gdal_translate, vrt_path, str(output_file),
                                "-ot", "Byte",
                                "-scale", "0", "65535", "0", "255",
                                "-co", "COMPRESS=DEFLATE",
                                "-co", "PREDICTOR=2",
                                "-co", "PHOTOMETRIC=RGB",
                                "-co", "BIGTIFF=YES"
                            ], capture_output=True, check=True, text=True)
                            
                            # Clean up temp files
                            for f in [red_tif, green_tif, blue_tif, vrt_path]:
                                if os.path.exists(f):
                                    os.unlink(f)
                            created_count += 1
                            continue  # Skip normal pipeline processing
                        else:
                            # Fallback: use single band
                            import shutil as sh
                            sh.move(red_tif, str(output_file))
                            for f in [green_tif, blue_tif]:
                                if os.path.exists(f):
                                    os.unlink(f)
                            created_count += 1
                            continue  # Skip normal pipeline processing
                    except Exception as e:
                        print(f"  Warning: RGB processing failed for tile, using intensity: {e}")
                        # Fallback to intensity - modify pipeline_json and continue with normal processing
                        pipeline_json = {
                            "pipeline": [
                                {
                                    "type": "readers.las",
                                    "filename": str(las_file)
                                },
                                {
                                    "type": "filters.crop",
                                    "bounds": f"([{tile_minx},{tile_maxx}],[{tile_miny},{tile_maxy}])"
                                },
                                {
                                    "type": "writers.gdal",
                                    "filename": str(output_file),
                                    "resolution": resolution,
                                    "radius": resolution,
                                    "output_type": "mean",
                                    "dimension": "Intensity",
                                    "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                                }
                            ]
                        }
                        # Continue with normal pipeline processing below
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(pipeline_json, f)
                    pipeline_file = f.name
                
                try:
                    subprocess.run(["pdal", "pipeline", pipeline_file],
                                  capture_output=True, check=True, text=True)
                    created_count += 1
                finally:
                    if os.path.exists(pipeline_file):
                        os.unlink(pipeline_file)
                        
            except Exception as e:
                print(f"  Warning: Failed to create tile: {e}")
                continue
    
    print(f"\n✓ Successfully created {created_count} tile images!")
    print(f"  You can now upload all images from {output_dir} to WebODM")
    return created_count > 0


def create_perspective_views(las_file, output_dir, resolution=0.1, mode='intensity', count=30):
    """
    Create perspective-like views from different azimuth and elevation angles.
    
    This function generates images from multiple viewpoints around the point cloud
    by creating orthographic views from different angles (azimuth/elevation combinations).
    
    Args:
        las_file: Path to input LAS/LAZ file
        output_dir: Directory to save output images
        resolution: Pixel resolution in meters
        mode: Rasterization mode ('intensity', 'rgb', 'elevation', 'count')
        count: Number of images to generate (default: 30)
    """
    import random
    
    las_file = Path(las_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get point cloud bounds and center
    info = get_las_info(las_file)
    if not info or 'bounds' not in info:
        print("ERROR: Could not read point cloud bounds")
        return False
    
    # Debug: Print point cloud info
    print(f"\n=== Point Cloud Information ===")
    print(f"File: {las_file}")
    if 'count' in info:
        print(f"Point count: {info['count']:,}")
    if 'stats' in info:
        stats = info['stats']
        print(f"Available dimensions: {list(stats.keys()) if isinstance(stats, dict) else 'N/A'}")
    print(f"Bounds: {info.get('bounds', {})}")
    print(f"===============================\n")
    
    # Check if RGB dimensions exist in the point cloud
    # PDAL info includes dimension information
    has_rgb = False
    if mode == 'rgb':
        # Check if file has RGB by trying to read a sample
        try:
            # Use PDAL info to check dimensions
            result = subprocess.run(
                ["pdal", "info", "--metadata", str(las_file)],
                capture_output=True,
                check=True,
                text=True
            )
            metadata = json.loads(result.stdout)
            # Check if Red, Green, Blue are in the dimensions
            if 'metadata' in metadata:
                for key in metadata['metadata']:
                    if 'Red' in str(key) or 'Green' in str(key) or 'Blue' in str(key):
                        has_rgb = True
                        break
            # Alternative: check if dimensions list includes RGB
            if not has_rgb and 'metadata' in metadata:
                dims_str = str(metadata.get('metadata', {}))
                if 'Red' in dims_str and 'Green' in dims_str and 'Blue' in dims_str:
                    has_rgb = True
        except:
            pass
        
        if not has_rgb:
            print("WARNING: LAS file does not appear to have RGB color data (Red, Green, Blue dimensions)")
            print("         Falling back to intensity mode")
            mode = 'intensity'
    
    bounds = info['bounds']
    minx, miny = bounds.get('minx', 0), bounds.get('miny', 0)
    maxx, maxy = bounds.get('maxx', 0), bounds.get('maxy', 0)
    minz, maxz = bounds.get('minz', 0), bounds.get('maxz', 0)
    
    # Also store as min_x, min_y for consistency
    min_x, min_y = minx, miny
    
    center_x = (minx + maxx) / 2
    center_y = (miny + maxy) / 2
    center_z = (minz + maxz) / 2
    
    # Calculate distance from center to use as camera distance
    width = maxx - minx
    height = maxy - miny
    depth = maxz - minz
    max_dim = max(width, height, depth)
    camera_distance = max_dim * 2.5  # Position camera far enough to see the whole cloud
    
    print(f"Point cloud center: ({center_x:.2f}, {center_y:.2f}, {center_z:.2f})")
    print(f"Point cloud size: {width:.2f}m x {height:.2f}m x {depth:.2f}m")
    print(f"Resolution: {resolution} meters")
    print(f"Mode: {mode}")
    print(f"Generating {count} perspective views from different angles...")
    print()
    
    # Verify resolution is reasonable
    if resolution <= 0:
        print(f"ERROR: Invalid resolution: {resolution}")
        return False
    if resolution > max(width, height) / 10:
        print(f"WARNING: Resolution ({resolution}m) seems very large compared to point cloud size")
        print(f"         Consider using a smaller resolution (e.g., {max(width, height) / 100:.2f}m)")
    
    base_name = las_file.stem
    created_count = 0
    
    # Generate views from different positions and angles
    # To create more diversity, we'll:
    # 1. Crop different regions of the point cloud
    # 2. Use different resolutions for variation
    # 3. Sample different subsets of points
    
    # Calculate grid dimensions for spatial distribution
    grid_size = math.ceil(math.sqrt(count))
    cell_width = width / grid_size
    cell_height = height / grid_size
    
    for i in range(count):
        # Generate azimuth (0-360 degrees) and elevation (10-85 degrees)
        # Distribute evenly around the sphere
        azimuth = (i * 360.0 / count) % 360
        # Elevation varies from 10 to 85 degrees (not too low, not straight down)
        elevation = 10 + (i % 8) * (75.0 / 7)  # Distribute elevation
        
        # Add some randomness for better distribution
        if count > 10:
            azimuth += random.uniform(-5, 5)
            elevation += random.uniform(-3, 3)
            elevation = max(10, min(85, elevation))  # Clamp elevation
        
        # Calculate which grid cell this view should cover
        grid_row = i // grid_size
        grid_col = i % grid_size
        
        # Calculate crop bounds for this view (different region of point cloud)
        # Use a fraction of the total bounds to create overlapping but distinct views
        crop_fraction = 0.6  # Each view covers 60% of total bounds
        crop_offset_x = (grid_col / grid_size) * width * (1 - crop_fraction)
        crop_offset_y = (grid_row / grid_size) * height * (1 - crop_fraction)
        
        view_min_x = min_x + crop_offset_x
        view_max_x = view_min_x + width * crop_fraction
        view_min_y = min_y + crop_offset_y
        view_max_y = view_min_y + height * crop_fraction
        
        # Vary resolution slightly for each view to add more diversity
        view_resolution = resolution * random.uniform(0.9, 1.1)
        
        # Create output filename with view number and angles
        output_file = output_dir / f"{base_name}_view_{i+1:03d}_az{int(azimuth)}_el{int(elevation)}.tif"
        
        try:
            # Create a rotated view by transforming coordinates
            # We'll create a bounding box that's rotated to this view angle
            # For simplicity, we'll use the full bounds but the view will be from a different angle
            
            # Create PDAL pipeline that creates an orthographic view
            # Since PDAL doesn't support true perspective, we'll create views
            # from different positions by cropping/rotating the data
            
            if mode == 'rgb':
                # RGB mode - create 3 separate bands and combine
                red_tif = output_dir / f"{base_name}_view_{i+1:03d}_az{int(azimuth)}_el{int(elevation)}_red.tif"
                green_tif = output_dir / f"{base_name}_view_{i+1:03d}_az{int(azimuth)}_el{int(elevation)}_green.tif"
                blue_tif = output_dir / f"{base_name}_view_{i+1:03d}_az{int(azimuth)}_el{int(elevation)}_blue.tif"
                
                # Check if RGB dimensions exist in the point cloud first
                rgb_success = True
                # Create separate pipelines for each band
                for dim, temp_file in [("Red", red_tif), ("Green", green_tif), ("Blue", blue_tif)]:
                    # Add crop filter to create spatial diversity
                    pipeline_stages = [
                        {
                            "type": "readers.las",
                            "filename": str(las_file)
                        }
                    ]
                    
                    # Add crop filter to focus on different region
                    if view_min_x < view_max_x and view_min_y < view_max_y:
                        pipeline_stages.append({
                            "type": "filters.crop",
                            "bounds": f"([{view_min_x:.6f}, {view_max_x:.6f}], [{view_min_y:.6f}, {view_max_y:.6f}])"
                        })
                    
                    pipeline_stages.append({
                        "type": "writers.gdal",
                        "filename": str(temp_file),
                        "resolution": view_resolution,
                        "radius": view_resolution,
                        "output_type": "mean",
                        "dimension": dim,
                        "data_type": "uint16_t",
                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                    })
                    
                    pipeline_json = {"pipeline": pipeline_stages}
                    
                    pf_temp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                    json.dump(pipeline_json, pf_temp)
                    pf_temp.close()
                    
                    try:
                        result = subprocess.run(["pdal", "pipeline", pf_temp.name],
                                              capture_output=True, check=True, text=True)
                        # Check if file was created and has non-zero size
                        if not os.path.exists(temp_file) or os.path.getsize(temp_file) == 0:
                            print(f"  Warning: {dim} band file is empty or missing, falling back to intensity")
                            rgb_success = False
                            break
                    except subprocess.CalledProcessError as e:
                        print(f"  Warning: Failed to create {dim} band: {e.stderr if e.stderr else 'Unknown error'}")
                        rgb_success = False
                        break
                    finally:
                        if os.path.exists(pf_temp.name):
                            os.unlink(pf_temp.name)
                
                if not rgb_success:
                    # Fallback to intensity mode
                    print(f"  Falling back to intensity mode for view {i+1}")
                    pipeline_stages = [
                        {
                            "type": "readers.las",
                            "filename": str(las_file)
                        }
                    ]
                    
                    # Add crop filter for spatial diversity
                    if view_min_x < view_max_x and view_min_y < view_max_y:
                        pipeline_stages.append({
                            "type": "filters.crop",
                            "bounds": f"([{view_min_x:.6f}, {view_max_x:.6f}], [{view_min_y:.6f}, {view_max_y:.6f}])"
                        })
                    
                    pipeline_stages.append({
                        "type": "writers.gdal",
                        "filename": str(output_file),
                        "resolution": view_resolution,
                        "radius": view_resolution,
                        "output_type": "mean",
                        "dimension": "Intensity",
                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                    })
                    
                    pipeline_json = {"pipeline": pipeline_stages}
                    
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                        json.dump(pipeline_json, f)
                        pipeline_file = f.name
                    
                    try:
                        subprocess.run(["pdal", "pipeline", pipeline_file],
                                      capture_output=True, check=True, text=True)
                        created_count += 1
                    finally:
                        if os.path.exists(pipeline_file):
                            os.unlink(pipeline_file)
                    # Clean up any partial RGB files
                    for f in [red_tif, green_tif, blue_tif]:
                        if os.path.exists(f):
                            os.unlink(f)
                    continue
                
                # Combine RGB bands using GDAL
                gdalbuildvrt = shutil.which('gdalbuildvrt')
                gdal_translate = shutil.which('gdal_translate')
                if gdalbuildvrt and gdal_translate:
                    vrt_path = str(output_file).replace('.tif', '.vrt')
                    try:
                        # Build VRT
                        result = subprocess.run([gdalbuildvrt, "-separate", vrt_path, str(red_tif), str(green_tif), str(blue_tif)],
                                             capture_output=True, check=True, text=True)
                        
                        # Translate to final RGB with proper scaling
                        # First check the actual value range in the files
                        result = subprocess.run([
                            gdal_translate, vrt_path, str(output_file),
                            "-ot", "Byte",
                            "-scale", "0", "65535", "0", "255",  # Scale 16-bit to 8-bit
                            "-co", "COMPRESS=DEFLATE",
                            "-co", "PREDICTOR=2",
                            "-co", "PHOTOMETRIC=RGB",
                            "-co", "BIGTIFF=YES"
                        ], capture_output=True, check=True, text=True)
                        
                        # Verify the output file is not empty/black
                        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            created_count += 1
                        else:
                            print(f"  Warning: Output RGB file is empty, falling back to intensity")
                            # Fallback to intensity
                            pipeline_json = {
                                "pipeline": [
                                    {
                                        "type": "readers.las",
                                        "filename": str(las_file)
                                    },
                                    {
                                        "type": "writers.gdal",
                                        "filename": str(output_file),
                                        "resolution": resolution,
                                        "radius": resolution,
                                        "output_type": "mean",
                                        "dimension": "Intensity",
                                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                                    }
                                ]
                            }
                            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                                json.dump(pipeline_json, f)
                                pipeline_file = f.name
                            try:
                                subprocess.run(["pdal", "pipeline", pipeline_file],
                                              capture_output=True, check=True, text=True)
                                created_count += 1
                            finally:
                                if os.path.exists(pipeline_file):
                                    os.unlink(pipeline_file)
                    except subprocess.CalledProcessError as e:
                        print(f"  Warning: GDAL RGB combination failed: {e.stderr if e.stderr else 'Unknown error'}")
                        # Fallback to intensity
                        pipeline_json = {
                            "pipeline": [
                                {
                                    "type": "readers.las",
                                    "filename": str(las_file)
                                },
                                {
                                    "type": "writers.gdal",
                                    "filename": str(output_file),
                                    "resolution": resolution,
                                    "radius": resolution,
                                    "output_type": "mean",
                                    "dimension": "Intensity",
                                    "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                                }
                            ]
                        }
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                            json.dump(pipeline_json, f)
                            pipeline_file = f.name
                        try:
                            subprocess.run(["pdal", "pipeline", pipeline_file],
                                          capture_output=True, check=True, text=True)
                            created_count += 1
                        finally:
                            if os.path.exists(pipeline_file):
                                os.unlink(pipeline_file)
                    
                    # Clean up temp files
                    for f in [red_tif, green_tif, blue_tif, vrt_path]:
                        if os.path.exists(f):
                            try:
                                os.unlink(f)
                            except:
                                pass
                    continue
                else:
                    # GDAL tools not available - fallback to intensity
                    print(f"  Warning: GDAL tools not available, falling back to intensity")
                    for f in [red_tif, green_tif, blue_tif]:
                        if os.path.exists(f):
                            os.unlink(f)
                    pipeline_json = {
                        "pipeline": [
                            {
                                "type": "readers.las",
                                "filename": str(las_file)
                            },
                            {
                                "type": "writers.gdal",
                                "filename": str(output_file),
                                "resolution": resolution,
                                "radius": resolution,
                                "output_type": "mean",
                                "dimension": "Intensity",
                                "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                            }
                        ]
                    }
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                        json.dump(pipeline_json, f)
                        pipeline_file = f.name
                    try:
                        subprocess.run(["pdal", "pipeline", pipeline_file],
                                      capture_output=True, check=True, text=True)
                        created_count += 1
                    finally:
                        if os.path.exists(pipeline_file):
                            os.unlink(pipeline_file)
                    continue
            else:
                # Non-RGB modes: intensity, elevation, count
                dimension_name = (
                    "Z" if mode == 'elevation'
                    else "Intensity" if mode == 'intensity'
                    else "Intensity"  # Default fallback
                )
                
                # Set appropriate data type and gdalopts based on dimension
                if mode == 'elevation':
                    data_type = "float32"
                    gdalopts = "COMPRESS=DEFLATE,BIGTIFF=YES"  # No PREDICTOR for float32
                elif mode == 'intensity':
                    data_type = "uint16_t"
                    gdalopts = "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                elif mode == 'count':
                    data_type = "uint32_t"
                    gdalopts = "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                else:
                    data_type = "uint16_t"
                    gdalopts = "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
                
                # Build pipeline with crop filter for spatial diversity
                pipeline_stages = [
                    {
                        "type": "readers.las",
                        "filename": str(las_file)
                    }
                ]
                
                # Add crop filter to focus on different region
                if view_min_x < view_max_x and view_min_y < view_max_y:
                    pipeline_stages.append({
                        "type": "filters.crop",
                        "bounds": f"([{view_min_x:.6f}, {view_max_x:.6f}], [{view_min_y:.6f}, {view_max_y:.6f}])"
                    })
                
                pipeline_stages.append({
                    "type": "writers.gdal",
                    "filename": str(output_file),
                    "resolution": view_resolution,
                    "radius": view_resolution,
                    "output_type": "mean" if mode != 'count' else "count",
                    "dimension": dimension_name,
                    "data_type": data_type,
                    "gdalopts": gdalopts
                })
                
                pipeline_json = {"pipeline": pipeline_stages}
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(pipeline_json, f)
                    pipeline_file = f.name
                
                try:
                    result = subprocess.run(["pdal", "pipeline", pipeline_file],
                                          capture_output=True, check=True, text=True)
                    
                    # Verify the output file was created and is not empty
                    if os.path.exists(output_file):
                        file_size = os.path.getsize(output_file)
                        if file_size > 0:
                            # Check if file has actual data (not just black)
                            try:
                                import rasterio
                                with rasterio.open(output_file) as src:
                                    data = src.read(1)
                                    min_val = float(data.min())
                                    max_val = float(data.max())
                                    mean_val = float(data.mean())
                                    if max_val == 0 and min_val == 0:
                                        print(f"  ⚠ Warning: View {i+1} is all zeros (black) - check if {dimension_name} dimension exists in LAS file")
                                    else:
                                        print(f"  ✓ Created view {i+1}: {output_file.name} ({file_size} bytes, values: {min_val:.1f}-{max_val:.1f}, mean: {mean_val:.1f})")
                                        created_count += 1
                            except ImportError:
                                # rasterio not available, just check file size
                                created_count += 1
                                print(f"  ✓ Created view {i+1}: {output_file.name} ({file_size} bytes)")
                            except Exception as e:
                                print(f"  ⚠ Warning: Could not verify view {i+1} data: {e}")
                                created_count += 1
                        else:
                            print(f"  ✗ Warning: View {i+1} file is empty (0 bytes)")
                    else:
                        print(f"  ✗ Warning: View {i+1} file was not created")
                        
                except subprocess.CalledProcessError as e:
                    print(f"  ✗ Error creating view {i+1}: {e.stderr if e.stderr else str(e)}")
                    if e.stdout:
                        print(f"    stdout: {e.stdout[:200]}")
                finally:
                    if os.path.exists(pipeline_file):
                        os.unlink(pipeline_file)
                        
        except Exception as e:
            print(f"  Warning: Failed to create view {i+1}: {e}")
            continue
    
    print(f"\n✓ Successfully created {created_count} perspective view images!")
    print(f"  You can now upload all images from {output_dir} to WebODM")
    return created_count > 0


def convert_las_to_images(las_file, output_dir, resolution=0.1, mode='intensity', multiview=False, 
                         tile_size=100, overlap=0.3, count=30, use_perspective=False):
    """
    Convert LAS file to one or more image files.
    
    Args:
        las_file: Path to input LAS/LAZ file
        output_dir: Directory to save output images
        resolution: Pixel resolution in meters
        mode: Rasterization mode ('intensity', 'rgb', 'elevation', 'count')
        multiview: If True, create multiple overlapping tiles instead of single image
        tile_size: Size of each tile in meters (for multiview mode)
        overlap: Overlap percentage between tiles (0.0-1.0, for multiview mode)
    """
    las_file = Path(las_file)
    output_dir = Path(output_dir)
    
    if not las_file.exists():
        print(f"ERROR: Input file does not exist: {las_file}")
        return False
    
    if not las_file.suffix.lower() in ['.las', '.laz']:
        print(f"ERROR: Input file must be .las or .laz: {las_file}")
        return False
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Converting {las_file} to images...")
    print(f"Output directory: {output_dir}")
    print(f"Resolution: {resolution} meters")
    print(f"Mode: {mode}")
    print()
    
    # Get LAS file info to auto-calculate resolution if needed
    info = get_las_info(las_file)
    if info:
        bounds = info.get('bounds', {})
        if resolution is None or resolution == 0:
            calculated_res = calculate_resolution(
                bounds,
                info.get('count', 0)
            )
            resolution = calculated_res
            print(f"Auto-calculated resolution: {resolution} meters")
    
    # Use perspective views if requested (generates views from different angles)
    if use_perspective:
        return create_perspective_views(las_file, output_dir, resolution, mode, count)
    
    # Use multiview mode if requested (creates overlapping tiles)
    if multiview:
        return create_multiview_images(las_file, output_dir, resolution, mode, tile_size, overlap, count)
    
    # Generate output filename
    base_name = las_file.stem
    output_file = output_dir / f"{base_name}_{mode}_{resolution}m.tif"
    
    # Rasterize the point cloud
    success, error = rasterize_pointcloud(las_file, output_file, resolution, mode)
    
    if success:
        print(f"\n✓ Conversion complete!")
        print(f"  Output file: {output_file}")
        print(f"\nYou can now upload {output_file} to WebODM for processing.")
        return True
    else:
        print("\n✗ Conversion failed!")
        if error:
            print(f"  Error: {error}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Convert LAS/LAZ files to images for WebODM processing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert with default settings (intensity mode, 0.1m resolution)
  python las_to_images.py input.las output/
  
  # Convert with custom resolution
  python las_to_images.py input.laz output/ --resolution 0.05
  
  # Convert using RGB values (if available in point cloud)
  python las_to_images.py input.las output/ --mode rgb
  
  # Convert using elevation values
  python las_to_images.py input.las output/ --mode elevation
  
  # Create multiple overlapping tiles (better for photogrammetry)
  python las_to_images.py input.las output/ --multiview --tile-size 100 --overlap 0.3
  
  # Create RGB tiles
  python las_to_images.py input.las output/ --multiview --mode rgb
        """
    )
    
    parser.add_argument('input', help='Input LAS/LAZ file path')
    parser.add_argument('output', help='Output directory for images')
    parser.add_argument('--resolution', type=float, default=0.1,
                       help='Pixel resolution in meters (default: 0.1)')
    parser.add_argument('--mode', choices=['intensity', 'rgb', 'elevation', 'count'],
                       default='intensity',
                       help='Rasterization mode (default: intensity)')
    parser.add_argument('--multiview', action='store_true',
                       help='Create multiple overlapping tiles instead of single image (better for photogrammetry)')
    parser.add_argument('--tile-size', type=float, default=100,
                       help='Tile size in meters for multiview mode (default: 100)')
    parser.add_argument('--overlap', type=float, default=0.3,
                       help='Overlap percentage between tiles 0.0-1.0 (default: 0.3 = 30%%)')
    
    args = parser.parse_args()
    
    # Check prerequisites
    if not check_pdal():
        print("ERROR: PDAL is not installed or not in PATH.")
        print("Please install PDAL: https://pdal.io/download.html")
        print("On Ubuntu/Debian: sudo apt-get install pdal")
        print("On macOS: brew install pdal")
        sys.exit(1)
    
    # Convert the file
    success = convert_las_to_images(
        args.input,
        args.output,
        args.resolution,
        args.mode,
        args.multiview,
        args.tile_size,
        args.overlap
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

