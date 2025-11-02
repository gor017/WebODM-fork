#!/usr/bin/env python3
"""
Convert GeoTIFF files to JPEG format for better WebODM compatibility.

WebODM's OpenSfM sometimes has trouble reading GeoTIFF files. This script
converts them to standard JPEG format while preserving visual information.

Usage:
    python convert_tif_to_jpg.py input_directory/ output_directory/
    python convert_tif_to_jpg.py output_directory/ output_jpg/ --format png
"""

import os
import sys
import argparse
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: PIL/Pillow is required. Install with: pip install Pillow")
    sys.exit(1)

try:
    import rasterio
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    from rasterio.enums import ColorInterp
except ImportError:
    print("WARNING: rasterio not available, will try basic conversion")
    rasterio = None


def convert_geotiff_to_jpg(input_file, output_file, format='JPEG', quality=95):
    """
    Convert GeoTIFF to JPEG/PNG format.
    
    Args:
        input_file: Input GeoTIFF file
        output_file: Output JPEG/PNG file
        format: 'JPEG' or 'PNG'
        quality: JPEG quality (1-100)
    """
    try:
        # Try to open as raster first (handles multi-band, georeferenced)
        if rasterio:
            try:
                with rasterio.open(input_file) as src:
                    # Read all bands
                    bands = []
                    for i in range(1, src.count + 1):
                        band = src.read(i)
                        # Normalize to 0-255
                        if band.dtype != np.uint8:
                            band_min = np.nanmin(band)
                            band_max = np.nanmax(band)
                            if band_max > band_min:
                                band = ((band - band_min) / (band_max - band_min) * 255).astype(np.uint8)
                            else:
                                band = np.zeros_like(band, dtype=np.uint8)
                        
                        # Handle NaN values
                        band = np.nan_to_num(band, nan=0).astype(np.uint8)
                        bands.append(band)
                    
                    # Handle different band counts
                    if len(bands) == 1:
                        # Grayscale
                        img_array = bands[0]
                        mode = 'L'
                    elif len(bands) >= 3:
                        # RGB - take first 3 bands
                        img_array = np.stack([bands[0], bands[1], bands[2]], axis=0)
                        mode = 'RGB'
                    else:
                        # Single band, duplicate for RGB
                        img_array = np.stack([bands[0], bands[0], bands[0]], axis=0)
                        mode = 'RGB'
                    
                    # Convert to PIL Image
                    if mode == 'L':
                        img = Image.fromarray(img_array, mode='L')
                    else:
                        # Transpose from (C, H, W) to (H, W, C)
                        img_array = np.transpose(img_array, (1, 2, 0))
                        img = Image.fromarray(img_array, mode='RGB')
                    
                    # Convert grayscale to RGB if needed for JPEG
                    if mode == 'L' and format == 'JPEG':
                        img = img.convert('RGB')
                    
                    # Save
                    img.save(output_file, format=format, quality=quality if format == 'JPEG' else None)
                    return True
                    
            except Exception as e:
                print(f"  Warning: Rasterio processing failed ({e}), trying PIL fallback...")
        
        # Fallback: Simple PIL conversion
        with Image.open(input_file) as img:
            # Convert to RGB if needed
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            elif img.mode == 'L' and format == 'JPEG':
                img = img.convert('RGB')
            
            # Save
            img.save(output_file, format=format, quality=quality if format == 'JPEG' else None)
            return True
            
    except Exception as e:
        print(f"ERROR: Failed to convert {input_file}: {e}")
        return False


def convert_directory(input_dir, output_dir, format='JPEG', pattern='*.tif'):
    """
    Convert all TIF files in a directory.
    
    Args:
        input_dir: Input directory with TIF files
        output_dir: Output directory for JPEG/PNG files
        format: 'JPEG' or 'PNG'
        pattern: File pattern to match (default: *.tif)
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    
    if not input_dir.exists():
        print(f"ERROR: Input directory does not exist: {input_dir}")
        return False
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Find all TIF files
    tif_files = list(input_dir.glob(pattern))
    if len(tif_files) == 0:
        tif_files = list(input_dir.glob('*.TIF'))
    
    if len(tif_files) == 0:
        print(f"ERROR: No TIF files found in {input_dir}")
        return False
    
    print(f"Found {len(tif_files)} TIF files to convert")
    print(f"Output format: {format}")
    print()
    
    converted = 0
    for tif_file in tif_files:
        # Create output filename
        if format == 'JPEG':
            output_file = output_dir / f"{tif_file.stem}.jpg"
        else:
            output_file = output_dir / f"{tif_file.stem}.png"
        
        print(f"Converting: {tif_file.name} -> {output_file.name}")
        
        if convert_geotiff_to_jpg(tif_file, output_file, format):
            converted += 1
            print(f"  ✓ Success")
        else:
            print(f"  ✗ Failed")
    
    print(f"\n✓ Successfully converted {converted}/{len(tif_files)} files")
    print(f"  Output directory: {output_dir}")
    print(f"\nUpload the {format} files to WebODM instead of the TIF files.")
    
    return converted > 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert GeoTIFF files to JPEG/PNG for WebODM compatibility',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert all TIFs to JPEG
  python convert_tif_to_jpg.py output_directory/ output_jpg/
  
  # Convert to PNG instead
  python convert_tif_to_jpg.py output_directory/ output_png/ --format png
  
  # Convert only view images
  python convert_tif_to_jpg.py output_directory/ output_jpg/ --pattern "*view*.tif"
        """
    )
    
    parser.add_argument('input', help='Input directory with TIF files')
    parser.add_argument('output', help='Output directory for JPEG/PNG files')
    parser.add_argument('--format', choices=['jpeg', 'jpg', 'png'], default='jpeg',
                       help='Output format (default: jpeg)')
    parser.add_argument('--pattern', default='*.tif',
                       help='File pattern to match (default: *.tif)')
    parser.add_argument('--quality', type=int, default=95,
                       help='JPEG quality 1-100 (default: 95)')
    
    args = parser.parse_args()
    
    format = 'JPEG' if args.format in ['jpeg', 'jpg'] else 'PNG'
    
    success = convert_directory(args.input, args.output, format, args.pattern)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

