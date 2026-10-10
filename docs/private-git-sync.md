# Private Git synchronization and authorship provenance

Date: 2026-10-10. Product baseline: GluSpot `6.412`, originally recorded at `c759803f694223f419e374ed7c7b30de02efb224` and tag `v6.412`.

The user explicitly requested a new local playback-source branch, synchronization to a private repository in their GitHub account, and replacement of both historical and future project commit identities with their own identity. The user also explicitly required a local backup of the original history.

## Identity and original history

The project-scoped identity is `LouisGluGlu <150634071+LouisGluGlu@users.noreply.github.com>`. This is the GitHub privacy address for the verified account; global Git identity is not changed.

The original reachable project history contained 46 commits under the Codex identity, 17 branches, one annotated release tag, and a stash. Before rewriting metadata, a complete `git bundle --all` backup is saved outside the repository under `E:/Glusopt_waitingforlaunch/git-backups/`, verified with `git bundle verify`, and accompanied by a SHA256 checksum, original refs and worktree HEAD records. The final backup also includes this research documentation commit. Old/new commit and tag mappings and the rewrite audit are retained beside the backup, rather than committed or uploaded.

The metadata rewrite preserves every original tree, message, timestamp, timezone and ordered parent relationship. Author and committer identities change; the annotated tag's target and tagger identity follow the corresponding mapping. The stash remains local with its content and parent structure preserved. Existing branches remain present. The shared branch/tag/stash refs use compare-and-swap updates; detached worktree HEADs require separate compare-and-swap operations and individual checks. This is not one globally atomic operation across worktrees.

Commit hashes necessarily change. Historical hashes in earlier release and verification documents describe the original evidence and can be resolved using the local backup and mapping. Their recorded source trees and earlier test results are not new verification of any playback implementation. Third-party source files, license notices and copyright attribution remain intact; changing Git metadata does not transfer third-party rights.

## Remote scope and confidentiality

The designated remote is `https://github.com/LouisGluGlu/GluSpot.git`, with repository visibility `private`. The new branch is `codex/owned-playback-sources`; `main` continues to contain the 6.412 product baseline, while the new branch contains the playback research and Git hygiene documentation.

Before uploading, verify the authenticated owner and the repository's `private` flag and visibility through GitHub. Check repository access, pending invitations, hooks, deploy keys and Pages, and disable Actions on this backup/research repository to prevent workflows from executing uploaded code automatically. After upload, verify remote branch/tag object IDs against the local refs and check private visibility again.

Push only explicit `refs/heads/*` and `refs/tags/*`. Do not mirror the entire local Git directory: stash, Codex runtime refs, original-history backups, reflogs, environments, generated caches, logs and temporary reference projects stay local. Private visibility is an access-control setting, not a promise that account compromise or a later human permission change is impossible.

The ignore rules cover runtime directories and common local credential files. The pre-upload audit checks reachable source history for obvious credential/private-key material, generated files and GitHub's 100 MiB file limit. Pattern checks have limits and do not replace careful review. New account adapters must keep cookies and tokens outside the repository, avoid secrets in URLs/logs, and use an appropriate protected local credential store.

This task establishes a reviewed Git baseline and source plan. It does not publish a website, create a public release, copy upstream player code, implement the online-playlist UI, or rebuild the executable.
