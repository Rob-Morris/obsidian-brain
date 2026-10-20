# Private local files

`src/brain-core/scripts/_bootstrap/private_files.py` owns filesystem privacy for local result
credentials and the directories containing retained output. Its stdlib-only
implementation has a POSIX leaf and a native Windows leaf. It does not extend
private consent-owner channels or change command permissions.

## Contract

| API | Behaviour |
| --- | --- |
| `create_dir(path)` | Create exactly one private directory with restrictive permissions/DACL at creation. An existing destination raises `PrivateFileError` with `errno == EEXIST`, without adoption or permission changes. |
| `protect_dir(path)` | Create one managed directory, or restrict a trusted-owned existing directory. The parent must exist. Do not use this to repair caller-selected credential files. |
| `validate_existing_dir(path)` | Validate an existing private directory without changing permissions. Reject links/reparse points in its path. |
| `open_private_file(path)` | Open the original leaf without following links/reparse points, validate its owner/access policy, and return a read-only fd. Caller reads through and closes that same fd. Only missing paths return `PrivateFileError.errno == ENOENT`. |
| `validate_fd(fd)` | Validate an opened regular file before reading sensitive bytes. Keep the descriptor open for the read; the helper does not close it. |
| `create_secret(path, data: bytes)` | Pin an existing parent without links/reparse points, create and validate a restrictive staged file before writing, then publish it without replacing an existing destination. Remove the temporary name on failure. |

All operations raise content-safe `PrivateFileError`, an `OSError` subclass, on
privacy or filesystem refusal. They do not echo credentials, filesystem paths
or native backend error messages. Existing destinations are never overwritten.
An interrupted publication can leave a private temporary file; it cannot expose
an incompletely written destination. An error after publication does not imply
that the destination is absent: callers must not replay source effects.

`validate_fd` proves the security of the opened object, not the pathname used
to obtain it. Use `open_private_file` for caller-selected credential paths; it
combines no-follow opening with security validation on the same object. Windows
uses `CreateFileW(OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)` before transferring
the validated native handle to a read-only CRT fd. Both read and staged-write CRT
conversions explicitly use `O_NOINHERIT`, so neither descriptor is an inherited
credential capability. Directory validation and creation pin ancestors for the
duration of each operation. A separate validation call is not a permanent lease
on a pathname or its permissions. Supply physical absolute paths: ancestor aliases
such as macOS `/tmp` (a symlink to `/private/tmp`) are intentionally refused.
If an established caller contract permits an alias, resolve the parent through
that contract before entering this helper; do not silently follow an untrusted
credential or directory leaf. Existing client directories must be validated,
not passed to `protect_dir` to repair a failed privacy check.

The CLI's `--result-session-file` contract permits parent aliases. Its adapter
resolves only the parent before calling this helper, preserving paths such as
`/tmp/session` while refusing a credential leaf that is itself a link. Retained
storage roots keep their separate strict path/binding checks.

A caller-selected credential's parent need not be private. POSIX 0755 repository
directories and Windows directories granting other users read/traverse access
are supported: privacy is established on the file before its first byte is
written. Creation does not change the parent's permissions. Managed automatic
client directories retain their separate private-directory validation contract.

## POSIX

Private objects must belong to the current user identified by `getuid()`, with
no group or other permission bits. Files must be regular. Hardlinks share the
same ownership/access policy and are permitted; link count is not credential
privacy proof. Creation uses 0700 directories and 0600 staged files;
managed protection applies 0700 only after checking ownership. Directory paths
are traversed using pinned descriptors and `O_NOFOLLOW`.

## Native Windows

Windows `stat` mode bits do not prove DACL privacy. Windows `chmod` changes only
the read-only attribute, and Python 3.12 `fchmod` is Unix-only. This leaf uses
Win32 handles and security descriptors instead, including on Python 3.12 versions
before the 3.12.4 `mkdir(0700)` change.

The trusted principals are the current process token's user SID, LocalSystem
(`S-1-5-18`) and built-in Administrators (`S-1-5-32-544`), matching the Windows
0700 boundary. An object's owner must be trusted. Its DACL must be present and
non-null, and every allow ACE must name a trusted SID. Ordinary deny ACEs do not
grant access and are accepted. Unknown, object-specific and callback ACE types
fail closed rather than being interpreted as ordinary allows. Inherit-only
allows are checked too, so child creation does not inherit an untrusted grant.
An empty DACL grants nobody access; ordinary OS access checks still determine
whether the caller can use that object.

New files/directories receive a protected DACL granting full access only to the
trusted principals, with inheritance for directory children. Credentials are
secured before their first byte is written. Existing caller-selected credentials
are checked without changing their ACL. Managed directory protection may replace
an actually unsafe DACL only after verifying trusted ownership. Healthy private
directories return after the shared owner/ACE inspection, without calling
`SetSecurityInfo` or propagating ACL changes through retained descendants. It
does not walk the cache to verify a healthy root. Foreign ownership, reparse
points and non-disk objects are refused. Hardlinked credentials retain the same DACL
and are permitted, including during no-overwrite publication. The helper
does not invoke PowerShell, `icacls` or another process on a page read.

This boundary excludes hostile processes already acting as the same OS user,
SYSTEM or an administrator. It does not promise that filesystem metadata survives
arbitrary external changes after validation. ACL-incapable filesystems or missing
security information fail closed; POSIX mode bits are never a Windows fallback.

## Verification

`tests/test_private_files.py` covers the conservative ACL policy independently of
Win32 and exercises real POSIX permissions, no-overwrite publication, hardlink
acceptance, failure cleanup and privacy before writing. `tests/test_private_files_windows.py`
uses real Win32 security descriptors for native creation/resumption, permissive
and null DACL refusal, non-repairing validation and managed-directory protection.
Its subprocess check proves helper resumption, not an installed CLI workflow.
Native symlink refusal requires a host allowed to create symlinks; that single
test reports a skip if Windows denies the privilege.

The Windows smoke workflow includes both files alongside installed CLI coverage.
Native tests skip on macOS/Linux; those skips are not native acceptance evidence.

Primary API references:

- [Python OS contracts](https://docs.python.org/3.12/library/os.html)
- [Win32 GetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo)
- [Win32 SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)
- [Win32 CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
