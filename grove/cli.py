"""Command line interface for Grove."""

import argparse
import sys
from typing import List, Optional

from grove.diff import format_diff
from grove.merge import format_conflicts
from grove.repo import Repo


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grove",
        description="Grove: Semantic Version Control for Config Files",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    parser_init = subparsers.add_parser("init", help="Initialize a new Grove repository")
    parser_init.add_argument("path", nargs="?", default=".", help="Directory to initialize")

    # commit
    parser_commit = subparsers.add_parser("commit", help="Commit changes to config files")
    parser_commit.add_argument("-m", "--message", default="Commit changes", help="Commit message")
    parser_commit.add_argument("files", nargs="*", help="Specific files to commit (optional)")

    # log
    parser_log = subparsers.add_parser("log", help="Show commit history")
    parser_log.add_argument("-n", "--limit", type=int, default=None, help="Limit number of commits shown")

    # diff
    parser_diff = subparsers.add_parser("diff", help="Show semantic diff between two commits")
    parser_diff.add_argument("commit_a", help="First commit hash (or base)")
    parser_diff.add_argument("commit_b", nargs="?", default=None, help="Second commit hash (defaults to HEAD)")

    # diff-working
    parser_diff_w = subparsers.add_parser("diff-working", help="Show semantic diff between working files and HEAD")
    parser_diff_w.add_argument("file", nargs="?", default=None, help="Optional specific file to check")

    # branch
    parser_branch = subparsers.add_parser("branch", help="List or create branches")
    parser_branch.add_argument("name", nargs="?", default=None, help="Branch name to create")

    # checkout
    parser_checkout = subparsers.add_parser("checkout", help="Switch branches or checkout commit")
    parser_checkout.add_argument("target", help="Branch name or commit hash to checkout")

    # merge
    parser_merge = subparsers.add_parser("merge", help="Merge another branch into current branch")
    parser_merge.add_argument("branch", help="Branch name to merge")

    parsed = parser.parse_args(args)

    try:
        if parsed.command == "init":
            repo = Repo.init(parsed.path)
            print(f"Initialized empty Grove repository in {repo.root_dir}/.grove")
            return 0

        repo = Repo.find_repo(".")

        if parsed.command == "commit":
            files = parsed.files if parsed.files else None
            commit_hash = repo.commit(files=files, message=parsed.message)
            branch_or_head, _ = repo.store.get_head()
            print(f"[{branch_or_head} {commit_hash[:7]}] {parsed.message}")
            return 0

        if parsed.command == "log":
            entries = repo.log(limit=parsed.limit)
            if not entries:
                print("No commits yet.")
                return 0
            for entry in entries:
                print(f"commit {entry['hash']}")
                print(f"Author: {entry.get('author', 'Grove User')}")
                print(f"Date:   {entry.get('timestamp')}")
                print(f"\n    {entry.get('message')}\n")
            return 0

        if parsed.command == "diff":
            commit_b = parsed.commit_b
            if commit_b is None:
                commit_b = repo.store.get_head_commit()
                if not commit_b:
                    print("Error: HEAD has no commits.", file=sys.stderr)
                    return 1
            diffs_by_file = repo.diff_commits(parsed.commit_a, commit_b)
            if not diffs_by_file:
                print("(no semantic differences)")
                return 0
            for filename, ops in diffs_by_file.items():
                print(f"diff --semantic a/{filename} b/{filename}")
                print(format_diff(ops))
            return 0

        if parsed.command == "diff-working":
            diffs_by_file = repo.diff_working(parsed.file)
            if not diffs_by_file:
                print("(no semantic differences)")
                return 0
            for filename, ops in diffs_by_file.items():
                print(f"diff --semantic working/{filename} HEAD/{filename}")
                print(format_diff(ops))
            return 0

        if parsed.command == "branch":
            if parsed.name is None:
                branches, current = repo.branch()
                for b in branches:
                    prefix = "* " if b == current else "  "
                    print(f"{prefix}{b}")
                return 0
            repo.branch(parsed.name)
            print(f"Created branch '{parsed.name}'")
            return 0

        if parsed.command == "checkout":
            repo.checkout(parsed.target)
            print(f"Switched to '{parsed.target}'")
            return 0

        if parsed.command == "merge":
            result = repo.merge_branch(parsed.branch)
            if result.status == "already-up-to-date":
                print("Already up to date.")
                return 0
            elif result.status == "fast-forward":
                print(f"Fast-forward merge to {result.commit_hash[:7]}")
                return 0
            elif result.status == "conflict":
                print("Automatic merge failed. Fix conflicts and commit the result.")
                print(format_conflicts(result.conflicts))
                return 1
            elif result.status == "merged":
                print(f"Merge successful: {result.commit_hash[:7]}")
                return 0
            else:
                raise RuntimeError(f"Unexpected merge status: '{result.status}'")

    except Exception as e:
        msg = e.args[0] if (isinstance(e, KeyError) and e.args) else str(e)
        print(f"Error: {msg}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
