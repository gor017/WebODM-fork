#!/usr/bin/env python3
"""
Create align.las/align.laz file for WebODM alignment.

This script copies your LAS/LAZ file and renames it to align.las/align.laz
so that WebODM can use it as a reference point cloud for georeferencing.

Usage:
    python create_align_las.py input.las [output_directory/]
    python create_align_las.py input.laz output_directory/
"""

import os
import sys
import shutil
import argparse
from pathlib import Path


def create_align_file(input_file, output_dir=None):
    """
    Create align.las or align.laz file from input LAS/LAZ file.
    
    Args:
        input_file: Path to input LAS/LAZ file
        output_dir: Optional output directory (default: same as input file)
    
    Returns:
        Path to created align file, or None if failed
    """
    input_file = Path(input_file)
    
    if not input_file.exists():
        print(f"ERROR: Input file does not exist: {input_file}")
        return None
    
    if input_file.suffix.lower() not in ['.las', '.laz']:
        print(f"ERROR: Input file must be .las or .laz: {input_file}")
        return None
    
    # Determine output location
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = input_file.parent
    
    # Create align filename
    align_file = output_dir / f"align{input_file.suffix.lower()}"
    
    # Copy the file
    try:
        shutil.copy2(input_file, align_file)
        print(f"✓ Created alignment file: {align_file}")
        print(f"  Original file: {input_file}")
        print(f"\nYou can now upload {align_file} to WebODM along with your photos.")
        print(f"The file will be automatically used for georeferencing during processing.")
        return align_file
    except Exception as e:
        print(f"ERROR: Failed to create alignment file: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Create align.las/align.laz file for WebODM alignment',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create align.las in same directory as input
  python create_align_las.py /path/to/your_file.las
  
  # Create align.laz in specific directory
  python create_align_las.py /path/to/your_file.laz output_directory/
  
  # For your specific file:
  python create_align_las.py /Users/harut/Downloads/Объёмы.las
        """
    )
    
    parser.add_argument('input', help='Input LAS/LAZ file path')
    parser.add_argument('output', nargs='?', default=None,
                       help='Output directory (optional, defaults to same directory as input)')
    
    args = parser.parse_args()
    
    result = create_align_file(args.input, args.output)
    
    sys.exit(0 if result else 1)


if __name__ == '__main__':
    main()

