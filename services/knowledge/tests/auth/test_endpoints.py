def test_register_user_success(client):
    register_payload = {
        "name": "John Doe",
        "user_id": "johndoe",
        "email": "johndoe@example.com",
        "password": "strongpassword123",
        "role": "user",
    }
    response = client.post("/auth/register", json=register_payload)
    assert response.status_code == 201, f"Failed registration: {response.text}"
    data = response.json()
    assert data["name"] == "John Doe"
    assert data["user_id"] == "johndoe"
    assert data["email"] == "johndoe@example.com"
    assert data["role"] == "user"
    assert "id" in data
    assert "password" not in data


def test_register_duplicate_user(client):
    register_payload = {
        "name": "Jane Doe",
        "user_id": "janedoe",
        "email": "janedoe@example.com",
        "password": "strongpassword123",
    }
    response1 = client.post("/auth/register", json=register_payload)
    assert response1.status_code == 201

    # Duplicate email
    response2 = client.post(
        "/auth/register",
        json={
            "name": "Different Jane",
            "user_id": "jane2",
            "email": "janedoe@example.com",
            "password": "password321",
        },
    )
    assert response2.status_code == 400
    assert "email already exists" in response2.json()["detail"].lower()


def test_login_success(client):
    register_payload = {
        "name": "John Doe",
        "user_id": "johndoe",
        "email": "johndoe@example.com",
        "password": "strongpassword123",
    }
    client.post("/auth/register", json=register_payload)

    response = client.post(
        "/auth/login",
        json={"email": "johndoe@example.com", "password": "strongpassword123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "johndoe"
    assert data["email"] == "johndoe@example.com"


def test_login_invalid_credentials(client):
    register_payload = {
        "name": "John Doe",
        "user_id": "johndoe",
        "email": "johndoe@example.com",
        "password": "strongpassword123",
    }
    client.post("/auth/register", json=register_payload)

    response = client.post(
        "/auth/login",
        json={"email": "johndoe@example.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_api_key_management_flow(client):
    # 1. Register User
    register_payload = {
        "name": "John Doe",
        "user_id": "johndoe",
        "email": "johndoe@example.com",
        "password": "strongpassword123",
    }
    client.post("/auth/register", json=register_payload)

    # 2. Generate API Key
    # FastAPI parses multiple body models as a nested JSON body
    key_payload = {
        "key_in": {"name": "Test Key"},
        "login_req": {"email": "johndoe@example.com", "password": "strongpassword123"},
    }
    response = client.post("/auth/api-keys", json=key_payload)
    assert response.status_code == 201, f"Failed generating key: {response.text}"
    data = response.json()
    assert "plain_key" in data
    assert "prefix" in data
    assert data["name"] == "Test Key"
    assert data["is_active"] is True

    plain_key = data["plain_key"]
    key_id = data["id"]
    prefix = data["prefix"]

    # 3. Access /auth/me with X-API-Key (Success)
    headers = {"X-API-Key": plain_key}
    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["user_id"] == "johndoe"

    # 4. Access /auth/me with invalid API Key (Failure)
    bad_headers = {"X-API-Key": "invalid_key"}
    bad_response = client.get("/auth/me", headers=bad_headers)
    assert bad_response.status_code == 401
    assert "invalid or inactive" in bad_response.json()["detail"].lower()

    # 5. List API keys
    list_response = client.get("/auth/api-keys", headers=headers)
    assert list_response.status_code == 200
    keys_list = list_response.json()
    assert len(keys_list) == 1
    assert keys_list[0]["prefix"] == prefix

    # 6. Revoke API Key
    revoke_response = client.delete(f"/auth/api-keys/{key_id}", headers=headers)
    assert revoke_response.status_code == 204

    # 7. Access /auth/me with Revoked API Key (Failure)
    revoked_response = client.get("/auth/me", headers=headers)
    assert revoked_response.status_code == 401


def test_role_based_access_control(client):
    # 1. Register User (user role)
    client.post(
        "/auth/register",
        json={
            "name": "Normal User",
            "user_id": "normaluser",
            "email": "normal@example.com",
            "password": "password123",
            "role": "user",
        },
    )

    # 2. Register Admin
    client.post(
        "/auth/register",
        json={
            "name": "Admin User",
            "user_id": "adminuser",
            "email": "admin@example.com",
            "password": "password123",
            "role": "admin",
        },
    )

    # 3. Generate key for Normal User
    normal_key_resp = client.post(
        "/auth/api-keys",
        json={
            "key_in": {"name": "Normal Key"},
            "login_req": {"email": "normal@example.com", "password": "password123"},
        },
    )
    normal_key = normal_key_resp.json()["plain_key"]

    # 4. Generate key for Admin User
    admin_key_resp = client.post(
        "/auth/api-keys",
        json={
            "key_in": {"name": "Admin Key"},
            "login_req": {"email": "admin@example.com", "password": "password123"},
        },
    )
    admin_key = admin_key_resp.json()["plain_key"]

    # 5. Normal user tries to change role (Forbidden)
    forbidden_resp = client.put(
        "/auth/users/normaluser/role",
        json={"role": "admin"},
        headers={"X-API-Key": normal_key},
    )
    assert forbidden_resp.status_code == 403

    # 6. Admin user changes normal user's role (Success)
    success_resp = client.put(
        "/auth/users/normaluser/role",
        json={"role": "admin"},
        headers={"X-API-Key": admin_key},
    )
    assert success_resp.status_code == 200
    assert success_resp.json()["role"] == "admin"

    # 7. Check /auth/me details for normaluser (who is now admin)
    me_resp = client.get("/auth/me", headers={"X-API-Key": normal_key})
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "admin"
