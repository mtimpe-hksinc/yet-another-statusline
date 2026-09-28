"""Tests for GitInfo._find_repo, _read_head, and from_cwd."""
import shutil
import subprocess
from pathlib import Path

import pytest

import statusline_command as sl



def _make_git_dir(base: Path, branch: str = 'main', commit: str = 'abcdef1234567890') -> Path:
    """Create a minimal .git directory structure in base."""
    gitdir = base / '.git'
    gitdir.mkdir(parents=True, exist_ok=True)
    (gitdir / 'HEAD').write_text(f'ref: refs/heads/{branch}\n')
    refs_heads = gitdir / 'refs' / 'heads'
    refs_heads.mkdir(parents=True, exist_ok=True)
    (refs_heads / branch).write_text(commit + '\n')
    return gitdir



def test_find_repo_walks_upward(tmp_path: Path) -> None:
    """_find_repo walks from a deep subdirectory up to where .git lives."""
    (tmp_path / '.git').mkdir()
    deep = tmp_path / 'a' / 'b' / 'c'
    deep.mkdir(parents=True)

    repo, gitdir = sl.GitInfo._find_repo(str(deep))
    assert repo == str(tmp_path)
    assert gitdir == str(tmp_path / '.git')


def test_find_repo_no_git_returns_empty(tmp_path: Path) -> None:
    """_find_repo returns ('', '') when no .git is found."""
    deep = tmp_path / 'x' / 'y'
    deep.mkdir(parents=True)
    repo, gitdir = sl.GitInfo._find_repo(str(deep))
    assert repo == ''
    assert gitdir == ''



def test_read_head_ref_branch(tmp_path: Path) -> None:
    """_read_head parses ref: HEAD and returns branch + 9-char commit."""
    gitdir = _make_git_dir(tmp_path, branch='main', commit='abcdef1234567890')
    branch, commit = sl.GitInfo._read_head(str(gitdir))
    assert branch == 'main'
    assert commit == 'abcdef123'  # first 9 chars



def test_read_head_detached(tmp_path: Path) -> None:
    """_read_head returns ('d:<sha[:7]>', '') for a detached HEAD."""
    sha = 'abcdef1234567890abcdef1234567890abcdef12'
    gitdir = tmp_path / '.git'
    gitdir.mkdir()
    (gitdir / 'HEAD').write_text(sha + '\n')

    branch, commit = sl.GitInfo._read_head(str(gitdir))
    assert branch == f'd:{sha[:7]}'
    assert commit == ''



def test_from_cwd_non_repo(tmp_path: Path) -> None:
    """from_cwd returns an empty GitInfo when no .git exists."""
    result = sl.GitInfo.from_cwd(str(tmp_path))
    assert result == sl.GitInfo(branch='', commit='', modified=0, untracked=0)



@pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')
def test_from_cwd_real_repo(tmp_path: Path) -> None:
    """from_cwd populates modified and untracked counts from a real repo."""
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(tmp_path), 'config', 'user.email', 'test@test.com'],
        check=True, capture_output=True,
    )
    subprocess.run(
        ['git', '-C', str(tmp_path), 'config', 'user.name', 'Test'],
        check=True, capture_output=True,
    )

    # Create and commit a tracked file
    tracked = tmp_path / 'tracked.txt'
    tracked.write_text('initial\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'tracked.txt'], check=True, capture_output=True)
    subprocess.run(
        ['git', '-C', str(tmp_path), 'commit', '-m', 'init'],
        check=True, capture_output=True,
    )

    # Modify the tracked file (modified count = 1)
    tracked.write_text('changed\n')

    # Create an untracked file (untracked count = 1)
    (tmp_path / 'untracked.txt').write_text('new\n')

    result = sl.GitInfo.from_cwd(str(tmp_path))
    assert result.modified == 1
    assert result.untracked == 1



requires_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not installed')


def _git(repo: Path, *args: str) -> str:
    """Run git in repo with a fixed identity; return stdout stripped."""
    r = subprocess.run(
        ['git', '-c', 'user.email=a@b', '-c', 'user.name=t', '-C', str(repo), *args],
        check=True, capture_output=True, text=True,
    )
    return r.stdout.strip()


def _scratch_repo(base: Path, branch: str = 'main') -> Path:
    """Create a real repo at base with one empty commit on branch."""
    base.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', branch, str(base)], check=True, capture_output=True)
    _git(base, 'commit', '-q', '--allow-empty', '-m', 'init')
    return base


@requires_git
def test_read_head_slashed_branch(tmp_path: Path) -> None:
    """A branch with a slash keeps its full name and resolves HEAD's own sha."""
    repo = _scratch_repo(tmp_path / 'r')
    _git(repo, 'checkout', '-q', '-b', 'feature/foo')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'second')

    branch, commit = sl.GitInfo._read_head(str(repo / '.git'))
    assert branch == 'feature/foo'
    assert commit == _git(repo, 'rev-parse', 'HEAD')[:9]


@requires_git
def test_read_head_ignores_orig_head_after_reset(tmp_path: Path) -> None:
    """After a hard reset the sha is HEAD's, never the ORIG_HEAD leftover."""
    repo = _scratch_repo(tmp_path / 'r')
    _git(repo, 'checkout', '-q', '-b', 'feature/foo')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'second')
    _git(repo, 'reset', '-q', '--hard', 'HEAD~1')

    head = _git(repo, 'rev-parse', 'HEAD')
    orig = _git(repo, 'rev-parse', 'ORIG_HEAD')
    assert head != orig

    branch, commit = sl.GitInfo._read_head(str(repo / '.git'))
    assert branch == 'feature/foo'
    assert commit == head[:9]
    assert commit != orig[:9]


@requires_git
def test_read_head_packed_refs(tmp_path: Path) -> None:
    """With the loose ref packed away the sha still comes from packed-refs."""
    repo = _scratch_repo(tmp_path / 'r')
    _git(repo, 'checkout', '-q', '-b', 'feature/foo')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'second')
    head = _git(repo, 'rev-parse', 'HEAD')
    _git(repo, 'pack-refs', '--all')
    assert not (repo / '.git' / 'refs' / 'heads' / 'feature' / 'foo').exists()

    branch, commit = sl.GitInfo._read_head(str(repo / '.git'))
    assert branch == 'feature/foo'
    assert commit == head[:9]


@requires_git
def test_from_cwd_linked_worktree(tmp_path: Path) -> None:
    """A linked worktree (.git is a file) reports its own branch and sha."""
    repo = _scratch_repo(tmp_path / 'r')
    wt = tmp_path / 'wt'
    _git(repo, 'worktree', 'add', '-q', str(wt), '-b', 'wtbranch')
    assert (wt / '.git').is_file()

    result = sl.GitInfo.from_cwd(str(wt))
    assert result.branch == 'wtbranch'
    assert result.commit == _git(wt, 'rev-parse', 'HEAD')[:9]
    assert not result.detached


@requires_git
def test_from_cwd_detached_head(tmp_path: Path) -> None:
    """A detached HEAD is still flagged detached and shows the short sha."""
    repo = _scratch_repo(tmp_path / 'r')
    _git(repo, 'checkout', '-q', '--detach')

    result = sl.GitInfo.from_cwd(str(repo))
    assert result.detached
    assert result.branch == _git(repo, 'rev-parse', 'HEAD')[:7]


def _reftable_repo(base: Path, branch: str = 'main') -> Path:
    """Like _scratch_repo but with reftable ref storage (HEAD is a stub)."""
    base.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['git', 'init', '-q', '--ref-format=reftable', '-b', branch, str(base)],
                       capture_output=True)
    if r.returncode != 0:
        pytest.skip('git lacks reftable support')
    _git(base, 'commit', '-q', '--allow-empty', '-m', 'init')
    return base


@requires_git
def test_read_head_reftable(tmp_path: Path) -> None:
    """Reftable repos report the real branch, not the 'refs/heads/.invalid' stub."""
    repo = _reftable_repo(tmp_path / 'r', branch='feature/foo')
    branch, commit = sl.GitInfo._read_head(str(repo / '.git'))
    assert branch == 'feature/foo'
    assert commit == _git(repo, 'rev-parse', 'HEAD')[:9]


@requires_git
def test_read_head_reftable_detached(tmp_path: Path) -> None:
    """A detached reftable HEAD matches the loose-ref shape: ('d:<sha[:7]>', '')."""
    repo = _reftable_repo(tmp_path / 'r')
    _git(repo, 'checkout', '-q', '--detach')
    branch, commit = sl.GitInfo._read_head(str(repo / '.git'))
    assert branch == 'd:' + _git(repo, 'rev-parse', 'HEAD')[:7]
    assert commit == ''


@requires_git
def test_from_cwd_reftable_linked_worktree(tmp_path: Path) -> None:
    """A linked worktree of a reftable repo resolves via its own gitdir."""
    repo = _reftable_repo(tmp_path / 'r')
    wt = tmp_path / 'wt'
    _git(repo, 'worktree', 'add', '-q', str(wt), '-b', 'wtbranch')
    result = sl.GitInfo.from_cwd(str(wt))
    assert result.branch == 'wtbranch'
    assert result.commit == _git(wt, 'rev-parse', 'HEAD')[:9]
