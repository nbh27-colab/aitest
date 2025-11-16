from pathlib import Path
import re

class FileNameProcessor:
    def __init__(self, input_path: str):
        self.path = Path(input_path)
        self.extension = self.path.suffix.replace('.', '')
        self.filename = self.path.stem
        self.filename_with_extension = self.path.name

    def get_extension(self):
        return self.extension
    
    def get_filename(self):
        return self.filename
    
    def get_filename_with_extension(self):
        return self.filename_with_extension
    
    def get_safe_filename_with_extension(self):
        safe_name = re.sub(r'[^\w\-_.]', '_', self.filename_with_extension)
        safe_name = re.sub(r'_+', '_', safe_name)
        return safe_name
    
    def get_all(self):
        return self.extension, self.filename, self.filename_with_extension
