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
        True if successful, False otherwise
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
                    print(f"Warning: Failed to create {dim} band: {e.stderr}")
                    raise
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
                print(f"RGB band creation failed: {e}")
                print("Falling back to intensity mode...")
                shutil.rmtree(tmpdir, ignore_errors=True)
                return rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')

            # Stack with GDAL and normalize 16-bit RGB to 8-bit
            gdalbuildvrt = shutil.which('gdalbuildvrt')
            gdal_translate = shutil.which('gdal_translate')
            if not gdalbuildvrt or not gdal_translate:
                print("GDAL tools not found, falling back to intensity.")
                shutil.rmtree(tmpdir, ignore_errors=True)
                return rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')

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
                return True
            except subprocess.CalledProcessError as e:
                print(f"GDAL stacking failed: {e.stderr}")
                print("Falling back to intensity mode...")
                shutil.rmtree(tmpdir, ignore_errors=True)
                return rasterize_pointcloud(las_file, output_file, resolution, mode='intensity')
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        elif mode == 'elevation':
            # Rasterize using Z/elevation values
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
                        "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
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
            
            print(f"✓ Successfully created: {output_file}")
            return True
            
        finally:
            # Clean up temporary pipeline file
            if os.path.exists(pipeline_file):
                os.unlink(pipeline_file)
                
    except subprocess.CalledProcessError as e:
        print(f"ERROR: PDAL processing failed: {e.stderr}")
        return False
    except Exception as e:
        print(f"ERROR: Failed to rasterize point cloud: {e}")
        return False


def create_multiview_images(las_file, output_dir, resolution=0.1, mode='intensity', 
                             tile_size=100, overlap=0.3):
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
    
    print(f"Point cloud bounds: {width:.2f}m x {height:.2f}m")
    print(f"Creating tiles: {tile_size:.2f}m with {overlap*100}% overlap")
    
    # Calculate tile step (with overlap)
    step = tile_size * (1 - overlap)
    
    # Calculate grid
    cols = math.ceil(width / step)
    rows = math.ceil(height / step)
    
    print(f"Grid: {cols} columns x {rows} rows = {cols * rows} images")
    print()
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    count = 0
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
                            "gdalopts": "COMPRESS=DEFLATE,PREDICTOR=2,BIGTIFF=YES"
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
                            count += 1
                            continue  # Skip normal pipeline processing
                        else:
                            # Fallback: use single band
                            import shutil as sh
                            sh.move(red_tif, str(output_file))
                            for f in [green_tif, blue_tif]:
                                if os.path.exists(f):
                                    os.unlink(f)
                            count += 1
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
                    count += 1
                finally:
                    if os.path.exists(pipeline_file):
                        os.unlink(pipeline_file)
                        
            except Exception as e:
                print(f"  Warning: Failed to create tile: {e}")
                continue
    
    print(f"\n✓ Successfully created {count} tile images!")
    print(f"  You can now upload all images from {output_dir} to WebODM")
    return count > 0


def convert_las_to_images(las_file, output_dir, resolution=0.1, mode='intensity', multiview=False, 
                         tile_size=100, overlap=0.3):
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
    
    # Use multiview mode if requested
    if multiview:
        return create_multiview_images(las_file, output_dir, resolution, mode, tile_size, overlap)
    
    # Generate output filename
    base_name = las_file.stem
    output_file = output_dir / f"{base_name}_{mode}_{resolution}m.tif"
    
    # Rasterize the point cloud
    success = rasterize_pointcloud(las_file, output_file, resolution, mode)
    
    if success:
        print(f"\n✓ Conversion complete!")
        print(f"  Output file: {output_file}")
        print(f"\nYou can now upload {output_file} to WebODM for processing.")
        return True
    else:
        print("\n✗ Conversion failed!")
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

