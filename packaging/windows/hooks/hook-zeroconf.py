"""Keep the LGPL discovery library replaceable in the onedir package."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

hiddenimports = collect_submodules("zeroconf")
datas = collect_data_files("zeroconf") + copy_metadata("zeroconf")
module_collection_mode = {"zeroconf": "py"}
