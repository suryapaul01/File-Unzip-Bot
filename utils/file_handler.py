import os
import zipfile
import rarfile
import py7zr
import tarfile
import shutil
import random
from pathlib import Path
from utils.helpers import get_file_extension, is_archive_file, progress_bar, format_size


async def download_file(client, message, progress_callback=None):
    """
    Download file from message with unique naming
    Returns: (file_path, file_size, file_name)
    """
    if not message.document and not message.video and not message.audio:
        return None, None, None
    
    file = message.document or message.video or message.audio
    file_name = getattr(file, 'file_name', f'file_{message.id}')
    file_size = file.file_size
    
    # Create downloads directory if not exists
    os.makedirs('downloads', exist_ok=True)
    
    # Generate unique filename: userid_random5digit_originalname
    # Handle cases where from_user is None (e.g., channel posts)
    user_id = message.from_user.id if (hasattr(message, 'from_user') and message.from_user) else message.chat.id
    random_id = random.randint(10000, 99999)
    unique_filename = f"{user_id}_{random_id}_{file_name}"
    
    # Download file
    file_path = await client.download_media(
        message,
        file_name=f"downloads/{unique_filename}",
        progress=progress_callback
    )
    
    return file_path, file_size, file_name


async def extract_archive(file_path, password=None):
    """
    Extract archive file to a shorter path to avoid Windows path limits
    Returns: (success: bool, extracted_dir: str, error_msg: str)
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    
    def _extract_sync(file_path, password, extract_dir, ext):
        """Synchronous extraction logic to run in thread"""
        # Extract based on file type
        if ext == 'zip':
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                if password:
                    zip_ref.setpassword(password.encode('utf-8'))
                zip_ref.extractall(extract_dir)
        
        elif ext == 'rar':
            with rarfile.RarFile(file_path, 'r') as rar_ref:
                if password:
                    rar_ref.setpassword(password)
                rar_ref.extractall(extract_dir)
        
        elif ext == '7z':
            # 7z requires password as string, not bytes
            if password:
                with py7zr.SevenZipFile(file_path, mode='r', password=password) as sz_ref:
                    sz_ref.extractall(extract_dir)
            else:
                with py7zr.SevenZipFile(file_path, mode='r') as sz_ref:
                    sz_ref.extractall(extract_dir)
        
        elif ext in ['tar', 'gz', 'bz2']:
            with tarfile.open(file_path, 'r:*') as tar_ref:
                tar_ref.extractall(extract_dir)
        
        else:
            raise ValueError(f"Unsupported archive format: .{ext}")
    
    try:
        file_name = os.path.basename(file_path)
        ext = get_file_extension(file_name)
        
        # Create extraction directory with VERY short path to avoid Windows 260 char limit
        # Use random ID instead of full filename
        random_id = random.randint(100000, 999999)
        extract_dir = f"downloads/ext_{random_id}"
        os.makedirs(extract_dir, exist_ok=True)
        
        # Run extraction in thread executor to avoid blocking
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            await loop.run_in_executor(
                executor,
                _extract_sync,
                file_path,
                password,
                extract_dir,
                ext
            )
        
        # Check if extraction was successful
        if not os.listdir(extract_dir):
            return False, None, "Archive is empty or extraction failed"
        
        return True, extract_dir, None
    
    except zipfile.BadZipFile:
        return False, None, "❌ File is corrupted or not a valid ZIP file"
    
    except NotImplementedError as e:
        # Unsupported compression method
        error_msg = str(e).lower()
        if 'compression' in error_msg:
            return False, None, (
                "❌ This ZIP file uses an advanced compression method that isn't supported.\n\n"
                "Try:\n"
                "• Re-compress the file using standard ZIP compression\n"
                "• Use 7-Zip format instead (.7z)\n"
                "• Extract manually and upload the files"
            )
        return False, None, f"❌ Unsupported feature: {str(e)}"
    
    except ValueError as e:
        # Unsupported format from _extract_sync
        return False, None, str(e)
    
    except RuntimeError as e:
        error_str = str(e).lower()
        if 'password' in error_str or 'encrypted' in error_str or 'bad password' in error_str:
            return False, None, f"❌ Incorrect password or password required\n\nError: {str(e)}"
        return False, None, f"❌ Extraction error: {str(e)}"
    
    except Exception as e:
        error_str = str(e).lower()
        # Show actual error for debugging
        if 'password' in error_str:
            return False, None, f"❌ Password error: {str(e)}"
        return False, None, f"❌ Error: {str(e)}"


async def get_all_files(directory, max_files=50):
    """
    Get all files from directory recursively
    Returns: list of file paths
    """
    files = []
    for root, dirs, filenames in os.walk(directory):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            files.append(file_path)
            
            if len(files) >= max_files:
                break
        
        if len(files) >= max_files:
            break
    
    return files


async def cleanup_files(paths):
    """Delete files and directories instantly"""
    import asyncio
    
    async def cleanup_single(path):
        try:
            await asyncio.sleep(0.1)  # Small delay to ensure file is released
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
        except Exception as e:
            print(f"Error cleaning up {path}: {e}")
    
    # Run cleanup tasks concurrently
    tasks = [cleanup_single(path) for path in paths]
    await asyncio.gather(*tasks, return_exceptions=True)


async def validate_file_type(filename):
    """Check if file is a supported archive"""
    return is_archive_file(filename)


async def get_file_info(file_path):
    """Get file information"""
    if not os.path.exists(file_path):
        return None
    
    stat = os.stat(file_path)
    return {
        'size': stat.st_size,
        'name': os.path.basename(file_path),
        'path': file_path
    }
