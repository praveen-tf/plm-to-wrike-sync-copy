# Create Private Repository

Create a new private GitHub repository from an existing repository and push all branches, commits, and tags to the new repository.

## Steps

Follow these steps to create a new private repository from an existing one:

1. **Ensure GitHub CLI is installed**

   * Check if `gh` is available: `which gh || gh --version`
   * If not installed, download and install manually:
     ```bash
     curl -fsSL https://github.com/cli/cli/releases/download/v2.62.0/gh_2.62.0_linux_amd64.tar.gz -o /tmp/gh.tar.gz
     tar -xzf /tmp/gh.tar.gz -C /tmp
     sudo mv /tmp/gh_2.62.0_linux_amd64/bin/gh /usr/local/bin/
     ```
   * Note: `apt-get install gh` may fail in environments with network restrictions

2. **Verify GitHub authentication**

   * Check authentication status: `gh auth status`
   * Should show logged in with a token that has `repo` scope
   * If not authenticated, authentication may be configured via `GH_TOKEN` environment variable

3. **Create the new private repository**

   * Use GitHub CLI to create: `gh repo create <owner>/<repo-name> --private --description "Description here"`
   * Example: `gh repo create jmiller558/new_repo_test --private --description "New private repository created from dotfiles"`
   * This creates an empty repository on GitHub
   * The command will output the repository URL

4. **Configure git to use GitHub CLI authentication**

   * Run: `gh auth setup-git`
   * This configures git to use GitHub CLI's credential helper, avoiding manual token management

5. **Create a fresh copy of main branch contents**

   * Create a temporary directory: `mkdir -p /tmp/fresh_repo && cd /tmp/fresh_repo`
   * Initialize a new git repository: `git init`
   * Copy all files except .git directory from source: `find /path/to/source/repo -maxdepth 1 ! -name '.git' ! -name '.' -exec cp -r {} . \;`
   * Alternatively, if already in source repo: `find . -maxdepth 1 ! -name '.git' ! -name '.' -exec cp -r {} /tmp/fresh_repo/ \;`
   * Disable commit signing for this repository: `git config commit.gpgsign false`
   * Stage all files: `git add .`
   * Create initial commit: `git commit -m "Initial commit from main branch"`
   * Rename default branch to main if needed: `git branch -M main`

6. **Push to the new repository**

   * Add new repository as remote: `git remote add origin https://github.com/<owner>/<repo-name>.git`
   * Push main branch: `git push -u origin main`
   * All files from the main branch are now in the new repository with fresh history

7. **Verify the new repository**

   * View repository details: `gh repo view <owner>/<repo-name>`
   * Check via API: `gh api repos/<owner>/<repo-name> | grep -E '"name"|"private"|"html_url"|"default_branch"'`
   * Confirm `"private": true` in the output
   * Repository URL: `https://github.com/<owner>/<repo-name>`

## Example Commands

```bash
# Install GitHub CLI (if needed)
curl -fsSL https://github.com/cli/cli/releases/download/v2.62.0/gh_2.62.0_linux_amd64.tar.gz -o /tmp/gh.tar.gz
tar -xzf /tmp/gh.tar.gz -C /tmp
sudo mv /tmp/gh_2.62.0_linux_amd64/bin/gh /usr/local/bin/

# Check authentication
gh auth status

# Create new private repository
gh repo create jmiller558/new_repo_test --private --description "New private repository created from dotfiles"

# Configure git to use gh authentication
gh auth setup-git

# Create fresh copy (assuming you're in the source repository)
mkdir -p /tmp/fresh_repo
find . -maxdepth 1 ! -name '.git' ! -name '.' -exec cp -r {} /tmp/fresh_repo/ \;
cd /tmp/fresh_repo

# Initialize and commit
git init
git config commit.gpgsign false
git add .
git commit -m "Initial commit from main branch"
git branch -M main

# Push to new repository
git remote add origin https://github.com/jmiller558/new_repo_test.git
git push -u origin main

# Verify
gh repo view jmiller558/new_repo_test
```
## Notes

* **GitHub CLI Installation**: In environments with `apt-get` network restrictions, direct binary download is more reliable than package manager installation
* **Authentication**: The `gh auth setup-git` command configures git to use GitHub CLI's credential helper, avoiding manual token management
* **File Copying**: The `find` command with `cp` is used instead of `rsync` as `rsync` may not be available in all environments
* **Commit Signing**: Disabling commit signing (`git config commit.gpgsign false`) prevents failures in environments with signing configurations that may not work in temporary repositories
* **Repository Visibility**: The `--private` flag ensures the repository is created as private. Omit this flag for public repositories
* **Remote Names**: Using a descriptive remote name (e.g., `new_repo`) rather than overwriting `origin` makes it clear which repository you're working with
* **Repository Ownership**: The repository will be created under the authenticated user's account.
