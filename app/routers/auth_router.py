from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..database import get_db
from .. import models, schemas
from ..auth import verify_password, hash_password, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/token", response_model=schemas.TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.AdminUser).filter(
        models.AdminUser.username == body.username
    ).first()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    user.last_login = datetime.utcnow()
    db.commit()

    # Remember me = 30-day token, otherwise 24 hours
    expire_minutes = 60 * 24 * 30 if body.remember_me else None
    token = create_access_token(user.username, expire_minutes=expire_minutes)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/change-password")
def change_password(
    body: schemas.ChangePasswordRequest,
    current_user: models.AdminUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "Password changed successfully"}
