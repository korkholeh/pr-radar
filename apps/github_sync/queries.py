"""GraphQL documents. Every document requests the rateLimit block so the client can feed it to the
budget before returning data (spec §5.3)."""

RATE_LIMIT_FIELD = "rateLimit { remaining resetAt cost }"

RATE_LIMIT_QUERY = f"""
query RateLimit {{
  {RATE_LIMIT_FIELD}
}}
"""

PR_NODE_FIELDS = """
  id
  number
  title
  body
  state
  isDraft
  baseRefName
  headRefName
  createdAt
  updatedAt
  mergedAt
  closedAt
  additions
  deletions
  changedFiles
  mergeCommit { oid }
  labels(first: 20) { nodes { name } }
  author { login }
  mergedBy { login }
"""

PULL_REQUESTS_QUERY = f"""
query PullRequests($owner: String!, $name: String!, $first: Int!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequests(first: $first, after: $after, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        {PR_NODE_FIELDS}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

PR_REVIEWS_QUERY = f"""
query PullRequestReviews($id: ID!, $first: Int!, $after: String) {{
  node(id: $id) {{
    ... on PullRequest {{
      reviews(first: $first, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id
          author {{ login }}
          state
          submittedAt
          body
          comments {{ totalCount }}
        }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

PR_REVIEW_THREADS_QUERY = f"""
query PullRequestReviewThreads($id: ID!, $first: Int!, $after: String) {{
  node(id: $id) {{
    ... on PullRequest {{
      reviewThreads(first: $first, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          comments(first: 1) {{
            nodes {{
              id
              author {{ login }}
              createdAt
              bodyText
            }}
          }}
        }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

PR_COMMITS_QUERY = f"""
query PullRequestCommits($id: ID!, $first: Int!, $after: String) {{
  node(id: $id) {{
    ... on PullRequest {{
      commits(first: $first, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          commit {{
            oid
            message
            authoredDate
            committedDate
            additions
            deletions
            author {{ user {{ login }} email }}
            committer {{ user {{ login }} email }}
            statusCheckRollup {{ state }}
          }}
        }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

PR_FILES_QUERY = f"""
query PullRequestFiles($id: ID!, $first: Int!, $after: String) {{
  node(id: $id) {{
    ... on PullRequest {{
      files(first: $first, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          path
          additions
          deletions
          changeType
        }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

PR_TIMELINE_QUERY = f"""
query PullRequestTimeline($id: ID!, $first: Int!, $after: String) {{
  node(id: $id) {{
    ... on PullRequest {{
      timelineItems(first: $first, after: $after, itemTypes: [READY_FOR_REVIEW_EVENT]) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          __typename
          ... on ReadyForReviewEvent {{ createdAt }}
        }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""

# Access is the only condition for a repository to be listed: a lead who can read a client-owned
# repository must be able to track it, so both the viewer's affiliation and the owner's are widened
# to everything GitHub offers instead of narrowing the list to repositories the account owns.
VIEWER_REPOSITORIES_QUERY = f"""
query ViewerRepositories($first: Int!, $after: String) {{
  viewer {{
    repositories(
      first: $first
      after: $after
      affiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
      ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
    ) {{
      totalCount
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        id
        name
        nameWithOwner
        isPrivate
        isArchived
        defaultBranchRef {{ name }}
        owner {{ id login __typename }}
      }}
    }}
  }}
  {RATE_LIMIT_FIELD}
}}
"""
