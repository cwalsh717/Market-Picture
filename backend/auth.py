"""Authentication: password hashing, JWT tokens, and auth routes."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from backend.config import (
    COOKIE_SECURE,
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES,
    JWT_SECRET,
)
from backend.db import User, get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    created_at: str


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user_id: int, email: str) -> str:
    """Create a signed JWT with user_id and email claims."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_current_user(request: Request) -> dict:
    """Read access_token cookie and return decoded payload, or raise 401."""
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(token)
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---------------------------------------------------------------------------
# Cookie helper
# ---------------------------------------------------------------------------


def _set_auth_cookie(response: Response, token: str) -> None:
    """Set the access_token httpOnly cookie."""
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=JWT_EXPIRE_MINUTES * 60,
        path="/",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def register(body: RegisterRequest, response: Response) -> dict:
    """Create a new user account and set JWT cookie."""
    email = body.email.strip().lower()

    if len(body.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    session = await get_session()
    try:
        # Check for existing user
        result = await session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        )
        if result.first() is not None:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists",
            )

        # Insert new user via ORM (avoids RETURNING dialect issues)
        now = datetime.now(timezone.utc).isoformat()
        user = User(
            email=email,
            password_hash=hash_password(body.password),
            created_at=now,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        token = create_access_token(user.id, email)
        _set_auth_cookie(response, token)

        return {"id": user.id, "email": email, "created_at": now}
    except HTTPException:
        raise
    except Exception:
        await session.rollback()
        logger.exception("Registration failed")
        raise HTTPException(status_code=500, detail="Registration failed")
    finally:
        await session.close()


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict:
    """Validate credentials and set JWT cookie."""
    email = body.email.strip().lower()

    session = await get_session()
    try:
        result = await session.execute(
            text("SELECT id, email, password_hash, created_at FROM users WHERE email = :email"),
            {"email": email},
        )
        row = result.mappings().first()

        if row is None or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password",
            )

        token = create_access_token(row["id"], row["email"])
        _set_auth_cookie(response, token)

        return {
            "id": row["id"],
            "email": row["email"],
            "created_at": row["created_at"],
        }
    finally:
        await session.close()


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the auth cookie."""
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    return {"status": "ok"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    """Return the current authenticated user."""
    return {
        "id": int(user["sub"]),
        "email": user["email"],
    }
