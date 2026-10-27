# Lambda test events

Each JSON file contains one event object. Paste the entire object into the AWS Lambda console's test-event editor.

- `create-todo.json`: create a To-Do and capture the returned `todoId`.
- `list-todos.json`: list items for `test-user-1`.
- `no-auth.json`: test the handler's missing-authorizer-context path.
- `list-user2.json`: list items for `test-user-2`; an empty result depends on test data.
- `update-todo.json`: replace `PASTE-TODO-ID` with an ID returned by the create test.
- `delete-todo.json`: replace `PASTE-TODO-ID` with the same real ID.

Run update and delete only with a valid item ID owned by `test-user-1`.
