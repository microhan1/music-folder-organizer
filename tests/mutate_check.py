"""Break one rule at a time in a copy of the program and check that the tests notice.

    python tests/mutate_check.py <work dir>

A suite that passes proves little until it fails on purpose (TrimPDF LESSONS 12). Each mutation names
the file, the exact text to replace, and the tests that must fail. A mutation that freezes the program
counts as caught too (the numbering loop used to hang)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TIMEOUT = 120  # seconds per mutation; longer means the mutation froze the program

MUTATIONS = [
    ("the number survives the path limit", "plan.py",
     "    d2, s2, cut = pattern_mod.fit(base, dirs, stem, ext, limit=pattern_mod.PATH_LIMIT - len(suffix))\n    return d2, s2 + suffix, cut",
     "    return pattern_mod.fit(base, dirs, stem + suffix, ext)",
     "tests/test_cases.py -k long_names"),
    ("no title: the file keeps its name", "plan.py",
     "    if item.keep_name or (not track.tags.title and pattern_mod.names_the_file_by_title(opts.pattern)):",
     "    if item.keep_name:", "tests/test_cases.py -k without_a_title"),
    ("a cue that stays behind releases the names", "plan.py",
     "                    if i.keep_name and i.moves and (i.truncated or not _cue_travels(i, companions))}",
     "                    if False}", "tests/test_cases.py -k cue_album"),
    ("a shortened cue name releases the name", "plan.py", "(i.truncated or not _cue_travels(i, companions))",
     "(not _cue_travels(i, companions))", "tests/test_cases.py -k shortened"),
    ("stand-in names stay across languages", "plan.py", "        if found and found != current:\n            result = {**result, key: found}",
     "        pass", "tests/test_cases.py -k stand_in"),
    ("a stand-in name in a file name is kept", "plan.py",
     "        parts.add(stem.split(\" - \")[0].strip())  # \"{artist} - {title}\": the stand-in name lives in the file name",
     "        pass", "tests/test_cases.py -k stand_in_names_survive"),
    ("a written disc number stays", "plan.py", "    multi |= _already_multi_disc(scan, opts, artist_map, multi)\n", "",
     "tests/test_cases.py -k disc_number_in_names"),
    ("copy again is quiet", "plan.py",
     "    if opts.mode == \"copy\" and _already_copied(item, dst, base, dirs, stem, ext, disk, scanned):\n        return\n", "",
     "tests/test_cases.py -k copy_run_again"),
    ("junctions and symlinks are not entered", "longpath.py", "                if is_dir and (e.is_symlink() or e.is_junction()):\n                    continue",
     "                pass", "tests/test_cases.py -k junction"),
    ("the spelling on the disk is a candidate", "artists.py",
     "            pool += [e for e in sorted(self.existing_exact)", "            pool += [e for e in []",
     "tests/test_cases.py -k artist_folder_spelling"),
    ("alias spellings on the disk are candidates", "artists.py",
     "{normalize(e)} | ({normalize(alias)} if alias else set())", "{normalize(e)}",
     "tests/test_cases.py -k alias_form"),
    ("the preferred script wins between spellings on the disk", "artists.py",
     "        return sorted(pool, key=lambda n: (rank(n), in_script(n), -self.counts[n], self._order.get(n, 0)))[0]",
     "        return sorted(pool, key=lambda n: (rank(n), -self.counts[n], self._order.get(n, 0)))[0]",
     "tests/test_cases.py -k preferred_script"),
    ("exact spellings rank first", "artists.py", "            if sanitize_name(n) in self.existing_exact:\n                return 0",
     "            if False:\n                return 0", "tests/test_cases.py -k artist_case_variant"),
    ("file name prefixes count as existing names", "session.py",
     "            names.add_name(stem.split(\" - \")[0].strip())", "            pass",
     "tests/test_cases.py -k artist_case_variant"),
]


def main() -> int:
    work = os.path.abspath(sys.argv[1])
    escaped = []
    for name, file, old, new, tests in MUTATIONS:
        copy = os.path.join(work, "m")
        if os.path.exists(copy):
            shutil.rmtree(copy)
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "build", "dist", "__pycache__", "*.exe", ".pytest_cache"))
        path = os.path.join(copy, file)
        src = open(path, encoding="utf-8", newline="").read()
        text = src.replace("\r\n", "\n")
        if old not in text:
            print(f"?? {name}: text not found in {file}")
            escaped.append(name)
            continue
        open(path, "w", encoding="utf-8", newline="").write(text.replace(old, new, 1))
        args = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests.split()]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run(args, cwd=copy, capture_output=True, text=True, encoding="utf-8", env=env,
                               timeout=TEST_TIMEOUT, stdin=subprocess.DEVNULL)
            caught = r.returncode != 0
        except subprocess.TimeoutExpired:
            caught = True
        print(("caught " if caught else "ESCAPED") + f"  {name}")
        if not caught:
            escaped.append(name)
    shutil.rmtree(os.path.join(work, "m"), ignore_errors=True)
    print(f"{len(MUTATIONS) - len(escaped)}/{len(MUTATIONS)} caught")
    return 1 if escaped else 0


if __name__ == "__main__":
    sys.exit(main())
