import os
import subprocess
import argparse
import sys
import shutil
import json
from pathlib import Path
from guessit import guessit

_root = Path(__file__).parent.parent
for _l in (_root / ".env").read_text(encoding="utf-8").splitlines() if (_root / ".env").exists() else []:
    if _l.strip() and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip())

# Path for MakeMKV on host
MAKEMKV_PATHS = [
    r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
    r"C:\Program Files\MakeMKV\makemkvcon64.exe",
]

# Path for FFmpeg/FFprobe inside tdarr container
FFMPEG_CONTAINER_BIN = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
FFPROBE_CONTAINER_BIN = "/usr/lib/jellyfin-ffmpeg/ffprobe"

# Host to Container Mount Mapping (Proxion Specific)
STASH_ROOT = os.environ.get("STASH_ROOT", str(Path(__file__).parent.parent / "stash"))
MOUNT_MAP = {
    os.path.join(STASH_ROOT, "I_Video"): "/media/video",
    os.path.join(STASH_ROOT, "I_Ingest"): "/media/ingest",
    os.path.join(STASH_ROOT, "media"): "/media",
    r"I:\video": "/media/video",
    r"I:\video\MOVIES": "/media/video/MOVIES",
    r"I:\video\MUSIC": "/media/video/MUSIC",
    r"I:\video\SHOWS": "/media/video/SHOWS",
    r"I:\Proxion_Ingest": "/media/ingest",
}

def find_makemkv():
    for path in MAKEMKV_PATHS:
        if os.path.exists(path):
            return path
    return None

def get_metadata(name):
    """Guess title and year from folder name."""
    guess = guessit(name)
    title = guess.get('title', name)
    year = guess.get('year')
    if year:
        return f"{title} ({year})"
    return title

def to_container_path(host_path):
    """Translate host path to tdarr container path based on MOUNT_MAP."""
    host_path = os.path.abspath(host_path)
    # Sort keys by length descending to match most specific path first
    sorted_mounts = sorted(MOUNT_MAP.items(), key=lambda x: len(x[0]), reverse=True)
    for host_prefix, container_prefix in sorted_mounts:
        if host_path.lower().startswith(host_prefix.lower()):
            rel = os.path.relpath(host_path, host_prefix)
            if rel == ".":
                return container_prefix
            return f"{container_prefix}/{rel.replace(os.sep, '/')}"
    return host_path.replace(os.sep, '/')

def is_streamable(file_path):
    """Check if file is already in a streamable format (H.264/HEVC in MKV/MP4)."""
    container_path = to_container_path(file_path)
    probe_cmd = [
        "docker", "exec", "tdarr",
        FFPROBE_CONTAINER_BIN, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", container_path
    ]
    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        
        streams = data.get('streams', [])
        video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
        if not video_stream:
            return False
            
        codec = video_stream.get('codec_name', '').lower()
        container = data.get('format', {}).get('format_name', '').lower()
        
        # Streamable codecs: h264, hevc (h265), vp9, av1
        # Streamable containers: matroska (mkv), mov/mp4
        streamable_codecs = ['h264', 'hevc', 'vp9', 'av1']
        streamable_containers = ['matroska', 'mov', 'mp4']
        
        is_codec_ok = any(c in codec for c in streamable_codecs)
        is_container_ok = any(ct in container for ct in streamable_containers)
        
        return is_codec_ok and is_container_ok
    except:
        return False

def transcode_and_verify(input_file, output_file, codec="libx264", dry_run=False):
    """Transcode and verify using FFmpeg inside docker."""
    if is_streamable(input_file):
        print(f"[-] skipping transcode for {os.path.basename(input_file)} (already streamable)")
        if not dry_run:
            shutil.copy2(input_file, output_file)
        return True

    if dry_run:
        print(f"[DRY-RUN] Would transcode {os.path.basename(input_file)} to {codec}")
        return True

    container_input = to_container_path(input_file)
    container_output = to_container_path(output_file)
    
    print(f"[*] Transcoding ({codec}): {os.path.basename(input_file)}")
    
    transcode_cmd = [
        "docker", "exec", "tdarr",
        FFMPEG_CONTAINER_BIN, "-i", container_input,
        "-map", "0",
        "-c:v", codec, "-crf", "20" if codec == "libx264" else "22",
        "-preset", "slow" if codec == "libx264" else "medium",
    ]
    
    if codec == "libx264":
        transcode_cmd.extend(["-tune", "film"])
        
    transcode_cmd.extend([
        "-vf", "yadif", # Deinterlace
        "-c:a", "copy", # Passthrough original audio
        "-c:s", "copy",
        "-y", container_output
    ])
    
    try:
        subprocess.run(transcode_cmd, check=True)
        return True
    except Exception as e:
        print(f"[!] Transcode failed: {e}")
    return False

def needs_decryption(source):
    """Check if source (disc/folder) is encrypted and needs MakeMKV."""
    # Heuristic: Folders with VIDEO_TS/BDMV usually need MakeMKV to deal with decryption/structure
    if os.path.isdir(source):
        has_vts = os.path.exists(os.path.join(source, "VIDEO_TS"))
        has_bdmv = os.path.exists(os.path.join(source, "BDMV"))
        return has_vts or has_bdmv
    if str(source).lower().endswith(".iso"):
        return True
    return False

def process_disc(source, target_dir, bin_path, codec="libx264", cleanup=False, dry_run=False):
    source = Path(source).absolute()
    clean_name = get_metadata(source.name)
    movie_target_dir = Path(target_dir) / clean_name
    
    if not dry_run:
        movie_target_dir.mkdir(parents=True, exist_ok=True)
    
    final_mkv = movie_target_dir / f"{clean_name}.mkv"
    if final_mkv.exists():
        print(f"[-] skipping {clean_name} (exists)")
        return True

    if not needs_decryption(source):
        # Flat files - just check streamability/transcode
        if source.is_file() and source.suffix.lower() in ['.mkv', '.mp4', '.avi', '.ts', '.m2ts']:
            return transcode_and_verify(source, final_mkv, codec, dry_run)
        return False

    if dry_run:
        print(f"[DRY-RUN] Would decrypt/ingest: {source.name}")
        return True

    print(f"[*] Decrypting/Ingesting: {source.name} as {clean_name}")
    temp_mkv = movie_target_dir / f"{clean_name}.lossless.mkv"
    
    ingest_cmd = [bin_path, "-r", "mkv", f"file:{source}", "all", str(movie_target_dir)]
    try:
        subprocess.run(ingest_cmd, check=True, capture_output=True)
        candidates = [c for c in movie_target_dir.glob("*.mkv") if c.name not in [temp_mkv.name, final_mkv.name]]
        if not candidates: raise FileNotFoundError("MakeMKV output missing.")
        
        main_feature = max(candidates, key=os.path.getsize)
        if temp_mkv.exists(): os.remove(temp_mkv)
        os.rename(main_feature, temp_mkv)
        for c in candidates: 
            if c.exists() and c != main_feature: os.remove(c)
        
        if transcode_and_verify(temp_mkv, final_mkv, codec):
            os.remove(temp_mkv)
            if cleanup:
                print(f"[!] SAFE DELETE: {source}")
                if source.is_dir(): shutil.rmtree(source)
                else: os.remove(source)
            return True
    except Exception as e:
        print(f"[!] Failed {source.name}: {e}")
    return False

def main():
    parser = argparse.ArgumentParser(description="Sovereign Media Ingest 3.0: Smart DRM & Streamability Filter.")
    parser.add_argument("source", nargs='+', help="One or more source directories (Movies, MUSIC, SHOWS)")
    parser.add_argument("--target", required=True, help="Target directory for library")
    parser.add_argument("--codec", default="libx264", choices=["libx264", "libx265"], help="Target video codec")
    parser.add_argument("--cleanup", action="store_true", help="Delete source on success")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    
    args = parser.parse_args()
    bin_path = find_makemkv()
    if not bin_path: sys.exit(1)

    for root_str in args.source:
        source_root = Path(root_str).absolute()
        if not source_root.exists():
            print(f"[!] Source root not found: {source_root}. Skipping.")
            continue
            
        print(f"[*] Scanning: {source_root}")
        targets = []
        
        # Smart Discovery
        for p in source_root.iterdir():
            # 1. Immediate Disc Structures
            if p.is_dir() and (any((p / d).exists() for d in ["VIDEO_TS", "BDMV"])):
                targets.append(p)
            # 2. ISO Files
            elif p.is_file() and p.suffix.lower() == ".iso":
                targets.append(p)
            # 3. Flat Video Files (might need transcode)
            elif p.is_file() and p.suffix.lower() in [".mkv", ".mp4", ".avi", ".ts", ".m2ts"]:
                targets.append(p)
            # 4. Deep search for nested discs if it's a high-level folder
            elif p.is_dir():
                for sub in p.rglob("*"):
                    if sub.name in ["VIDEO_TS", "BDMV"] and sub.is_dir():
                        if sub.parent not in targets:
                            targets.append(sub.parent)

        print(f"[*] Found {len(targets)} candidates.")
        for t in targets:
            process_disc(t, args.target, bin_path, args.codec, args.cleanup, args.dry_run)

if __name__ == "__main__":
    main()

