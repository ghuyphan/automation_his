import os
import shutil
import zipfile

def build_portable_package():
    print("=========================================")
    print("HIS Automator - Portable Packager")
    print("=========================================")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(base_dir, "build_portable")
    portable_dir = os.path.join(build_dir, "HIS_Automator_Portable")
    
    if not os.path.exists(portable_dir):
        print(f"Error: Target portable directory not found at {portable_dir}")
        return
        
    print("Assembling source files...")
    
    # Files to copy directly into portable folder
    files_to_copy = [
        "gui.py",
        "automate.py",
        "co_button.png",
        "image3_icon.png",
        "requirements.txt",
        "CHANGELOG.txt"
    ]
    
    for filename in files_to_copy:
        src = os.path.join(base_dir, filename)
        dest = os.path.join(portable_dir, filename)
        if os.path.exists(src):
            shutil.copy2(src, dest)
            print(f"Copied: {filename}")
        else:
            print(f"Warning: Source file {filename} not found!")
            
    # Copy automation/ module package
    src_auto_dir = os.path.join(base_dir, "automation")
    dest_auto_dir = os.path.join(portable_dir, "automation")
    
    if os.path.exists(dest_auto_dir):
        shutil.rmtree(dest_auto_dir)
        
    shutil.copytree(src_auto_dir, dest_auto_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    print("Copied: automation/ package module (clean)")
    
    # Remove any layout profiles or debug configurations inside the portable folder to avoid clutter
    profile_path = os.path.join(portable_dir, "layout_profiles.json")
    if os.path.exists(profile_path):
        os.remove(profile_path)
        print("Removed leftover layout profiles from package.")
        
    debug_path = os.path.join(portable_dir, "debug")
    if os.path.exists(debug_path):
        shutil.rmtree(debug_path)
        print("Removed leftover debug directories from package.")
        
    # Compress portable directory into ZIP package
    zip_path = os.path.join(build_dir, "HIS_Automator_Portable.zip")
    print(f"Compressing package to {zip_path}...")
    
    if os.path.exists(zip_path):
        os.remove(zip_path)
        
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(portable_dir):
            for file in files:
                filepath = os.path.join(root, file)
                # Store relative path in zip
                relpath = os.path.relpath(filepath, build_dir)
                zipf.write(filepath, relpath)
                
    print("Packaging successfully completed!")
    print(f"Final zip size: {os.path.getsize(zip_path) / (1024*1024):.2f} MB")

if __name__ == "__main__":
    build_portable_package()
