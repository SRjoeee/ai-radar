#!/usr/bin/env python3
"""Sync Findit-AI research kanban items to a local JSON cache.

Requires: gh CLI with read:project scope.
Output: data/findit-kanban.json
"""

import json
import subprocess
import sys
from pathlib import Path

ORG = "Findit-AI"
PROJECT_NUMBER = 2
EXCLUDE_ACTIONS = {"Exclude"}
EXCLUDE_STATUSES = {"Low Signal", "Archived"}

QUERY = """
query($cursor: String) {
  organization(login: "%s") {
    projectV2(number: %d) {
      items(first: 100, after: $cursor) {
        nodes {
          content {
            ... on Issue { title url repository { nameWithOwner } }
            ... on PullRequest { title url repository { nameWithOwner } }
            ... on DraftIssue { title }
          }
          fieldValues(first: 15) {
            nodes {
              ... on ProjectV2ItemFieldTextValue {
                field { ... on ProjectV2Field { name } }
                text
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                field { ... on ProjectV2SingleSelectField { name } }
                name
              }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""" % (ORG, PROJECT_NUMBER)


def run_gh_query(cursor: str = "") -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}"]
    if cursor:
        cmd += ["-f", f"cursor={cursor}"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def extract_items(data: dict) -> list[dict]:
    nodes = data["data"]["organization"]["projectV2"]["items"]["nodes"]
    items = []
    for node in nodes:
        content = node.get("content") or {}
        title = content.get("title", "")
        issue_url = content.get("url", "")
        repo = content.get("repository", {}).get("nameWithOwner", "")

        fields = {}
        for fv in node.get("fieldValues", {}).get("nodes", []):
            fname = fv.get("field", {}).get("name", "")
            if fname:
                fields[fname] = fv.get("name") or fv.get("text", "")

        status = fields.get("Status", "")
        action = fields.get("Research Action", "")

        if action in EXCLUDE_ACTIONS or status in EXCLUDE_STATUSES:
            continue

        url = fields.get("URL", "") or issue_url
        if not url:
            continue

        items.append({
            "title": title,
            "url": url,
            "repo": repo,
            "status": status,
            "action": action,
            "direction": fields.get("Direction", ""),
            "priority": fields.get("Priority", ""),
            "category": fields.get("Category", ""),
        })
    return items


def main():
    all_items = []
    cursor = ""
    while True:
        data = run_gh_query(cursor)
        page = data["data"]["organization"]["projectV2"]["items"]
        all_items.extend(extract_items(data))
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    # Extract unique GitHub repos
    repos = set()
    for item in all_items:
        url = item["url"]
        if "github.com" in url and "/issues/" not in url and "/pull/" not in url:
            parts = url.rstrip("/").split("/")
            if len(parts) >= 5:
                repos.add(f"{parts[3]}/{parts[4]}")

    output = {
        "items": all_items,
        "repos": sorted(repos),
        "total_items": len(all_items),
        "total_repos": len(repos),
    }

    out_path = Path("data/findit-kanban.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Synced {len(all_items)} items, {len(repos)} repos -> {out_path}")


if __name__ == "__main__":
    main()
