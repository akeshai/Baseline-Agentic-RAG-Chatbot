# Authentication Service Routes (`app/auth`)

These routes handle user profile registrations, logins, secure API Key management, and role administration.

---

## Endpoints

### POST `/auth/register`
*   **Description**: Registers a new user profile.
*   **Authentication**: None
*   **Request Body (`UserCreate` schema)**:
    ```json
    {
      "name": "John Doe",
      "user_id": "johndoe",
      "email": "johndoe@example.com",
      "password": "strongpassword123",
      "role": "user"
    }
    ```
*   **Response Body (`UserResponse` schema - `201 Created`)**:
    ```json
    {
      "id": 1,
      "name": "John Doe",
      "user_id": "johndoe",
      "email": "johndoe@example.com",
      "role": "user"
    }
    ```

### POST `/auth/login`
*   **Description**: Verifies user credentials and returns profile details.
*   **Authentication**: None
*   **Request Body (`LoginRequest` schema)**:
    ```json
    {
      "email": "johndoe@example.com",
      "password": "strongpassword123"
    }
    ```
*   **Response Body (`UserResponse` schema - `200 OK`)**:
    ```json
    {
      "id": 1,
      "name": "John Doe",
      "user_id": "johndoe",
      "email": "johndoe@example.com",
      "role": "user"
    }
    ```

### POST `/auth/api-keys`
*   **Description**: Generates a secure, cryptographically random API Key.
*   **Authentication**: None (Requires credentials verification in the request body).
*   **Request Body**:
    - `key_in` (`ApiKeyCreate` schema): Friendly identifier name for the key.
    - `login_req` (`LoginRequest` schema): Email and password.
    ```json
    {
      "key_in": {
        "name": "Developer Key"
      },
      "login_req": {
        "email": "johndoe@example.com",
        "password": "strongpassword123"
      }
    }
    ```
*   **Response Body (`ApiKeyCreateResponse` schema - `201 Created`)**:
    *   *Note: The `plain_key` is only returned once on creation. Keep it safe.*
    ```json
    {
      "id": 1,
      "name": "Developer Key",
      "prefix": "sk_live_abc123",
      "is_active": true,
      "plain_key": "sk_live_abc123_randomsecrettokenhere..."
    }
    ```

### GET `/auth/api-keys`
*   **Description**: Lists the metadata of all API keys owned by the user.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`List[ApiKeyResponse]` - `200 OK`)**:
    ```json
    [
      {
        "id": 1,
        "name": "Developer Key",
        "prefix": "sk_live_abc123",
        "is_active": true
      }
    ]
    ```

### DELETE `/auth/api-keys/{id}`
*   **Description**: Revokes (deletes) a specific API key belonging to the user.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response**: `204 No Content`

### GET `/auth/me`
*   **Description**: Returns user profile details for a valid API key bearer.
*   **Headers Required**: `X-API-Key: <your_plain_key>`
*   **Response Body (`UserResponse` schema - `200 OK`)**:
    ```json
    {
      "id": 1,
      "name": "John Doe",
      "user_id": "johndoe",
      "email": "johndoe@example.com",
      "role": "user"
    }
    ```

### PUT `/auth/users/{user_id}/role`
*   **Description**: Modifies a user's role. Restricted to administrators (`admin` role).
*   **Headers Required**: `X-API-Key: <admin_plain_key>`
*   **Request Body (`UserRoleUpdate` schema)**:
    ```json
    {
      "role": "admin"
    }
    ```
*   **Response Body (`UserResponse` schema - `200 OK`)**:
    ```json
    {
      "id": 2,
      "name": "Target User",
      "user_id": "targetusername",
      "email": "target@example.com",
      "role": "admin"
    }
    ```
