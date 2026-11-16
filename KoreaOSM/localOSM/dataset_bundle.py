"""Restore the exact offline dataset from split, Git LFS-managed ZIP parts."""
import bisect
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import tempfile
import zipfile


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(4*1024*1024):digest.update(block)
    return digest.hexdigest()


class SplitReader(io.RawIOBase):
    """Seekable ZIP reader without creating a second joined archive on disk."""
    def __init__(self,paths):
        super().__init__();self.paths=paths;self.offsets=[0];self.position=0;self.current=None;self.index=None
        for path in paths:self.offsets.append(self.offsets[-1]+path.stat().st_size)
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=os.SEEK_SET):
        new=offset if whence==os.SEEK_SET else self.position+offset if whence==os.SEEK_CUR else self.offsets[-1]+offset if whence==os.SEEK_END else -1
        if new<0:raise ValueError('Invalid archive seek')
        self.position=new;return new
    def read(self,size=-1):
        remaining=self.offsets[-1]-self.position
        size=remaining if size<0 else min(size,remaining)
        if size<=0:return b''
        chunks=[]
        while size:
            index=bisect.bisect_right(self.offsets,self.position)-1
            if index!=self.index:
                if self.current:self.current.close()
                self.current=self.paths[index].open('rb');self.index=index
            self.current.seek(self.position-self.offsets[index])
            block=self.current.read(min(size,self.offsets[index+1]-self.position))
            if not block:raise ValueError('Dataset archive is truncated')
            chunks.append(block);self.position+=len(block);size-=len(block)
        return b''.join(chunks)
    def close(self):
        if self.current:self.current.close()
        super().close()


def data_path(root,name):
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError('Unsafe dataset path')
    if not(name.startswith(('localOSM/national_data/','localOSM/transit_data/')) or name=='localOSM/english_names.json'):
        raise ValueError('Unexpected dataset path')
    destination=(root/relative).resolve()
    if not destination.is_relative_to(root):raise ValueError('Dataset path escapes the application')
    return destination


def ensure_data(root=None,verify=False):
    root=(Path(root) if root is not None else Path(__file__).resolve().parents[1]).resolve()
    directory=root/'data';manifest_path=directory/'manifest.json'
    # A source checkout with an unpacked dataset is also supported.
    if not manifest_path.exists():
        if (root/'localOSM/national_data/manifest.json').is_file():return
        raise ValueError('Dataset manifest is missing. Clone the complete repository branch.')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format')!='koreaosm-data-bundle-v1':raise ValueError('Unsupported dataset bundle')
    expected=manifest['files'];missing=[]
    for entry in expected:
        destination=data_path(root,entry['path'])
        valid=destination.is_file() and destination.stat().st_size==entry['size']
        if valid and verify:valid=sha256(destination)==entry['sha256']
        if not valid:missing.append(entry)
    if not missing:
        if verify:print(f'Verified {len(expected)} dataset files.',flush=True)
        return
    parts=[]
    for entry in manifest['parts']:
        name=entry['name']
        if Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Unsafe archive part name')
        path=directory/name
        if not path.is_file():raise ValueError('Dataset parts are missing. Install Git LFS and run: git lfs pull')
        with path.open('rb') as stream:pointer=stream.read(64)
        if pointer.startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise ValueError('Git LFS data has not been downloaded. Run: git lfs pull')
        if path.stat().st_size!=entry['size'] or sha256(path)!=entry['sha256']:
            raise ValueError('Dataset part checksum failed. Download the data again with git lfs pull.')
        parts.append(path)
    print(f'Restoring {len(missing)} offline dataset files (first launch only)...',flush=True)
    try:
        with SplitReader(parts) as combined,zipfile.ZipFile(combined) as archive:
            names=archive.namelist()
            if len(names)!=len(expected) or set(names)!={e['path'] for e in expected}:
                raise ValueError('Dataset archive entries do not match the manifest')
            for entry in missing:
                destination=data_path(root,entry['path']);destination.parent.mkdir(parents=True,exist_ok=True)
                if not destination.parent.resolve().is_relative_to(root):raise ValueError('Unsafe dataset directory')
                descriptor,temp_name=tempfile.mkstemp(prefix=destination.name+'.restore-',suffix='.tmp',dir=destination.parent)
                temporary=Path(temp_name);digest=hashlib.sha256();size=0
                try:
                    with os.fdopen(descriptor,'wb') as output,archive.open(entry['path']) as stream:
                        while block:=stream.read(4*1024*1024):
                            output.write(block);digest.update(block);size+=len(block)
                    if size!=entry['size'] or digest.hexdigest()!=entry['sha256']:
                        raise ValueError('Restored dataset checksum failed: '+entry['path'])
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    except zipfile.BadZipFile as error:
        raise ValueError('The dataset ZIP archive is invalid. Run git lfs pull again.') from error
    print('Offline dataset restored and verified.',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify',action='store_true')
    args=parser.parse_args();ensure_data(verify=args.verify)
