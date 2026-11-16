"""Stable English console diagnostics, independent of Windows display language."""
import errno


def error_text(error):
    winerror=getattr(error,'winerror',None)
    code=getattr(error,'errno',None)
    if winerror==10048 or code==errno.EADDRINUSE:
        return 'The selected port is already in use. Stop the existing map server or choose another port.'
    if winerror in (5,10013) or code in (errno.EACCES,errno.EPERM):
        return 'Access was denied. Check folder permissions or choose a different port.'
    if winerror in (2,3) or code==errno.ENOENT:
        return 'A required file or folder was not found. Check that the offline data folders are installed.'
    if code==errno.ENOSPC:
        return 'There is not enough free disk space.'
    if winerror==32 or code==errno.EBUSY:
        return 'A required file is in use by another process.'
    if isinstance(error,OSError):
        identifier=f'Windows error {winerror}' if winerror is not None else f'OS error {code}'
        return f'The operating system could not complete the request ({identifier}).'
    message=str(error)
    if any('\uac00'<=ch<='\ud7a3' for ch in message):
        return f'Could not load the offline map data ({type(error).__name__}).'
    return message
