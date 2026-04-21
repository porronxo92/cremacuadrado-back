"""
Users API endpoints - Profile, Addresses.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import DbSession, CurrentUser
from app.models.user import User, Address
from app.schemas.user import (
    UserResponse, UserUpdate, AddressCreate, AddressUpdate, AddressResponse,
    PasswordChange
)
from app.schemas.common import Message
from app.utils.security import get_password_hash, verify_password

router = APIRouter()


# =============================================================================
# Profile endpoints
# =============================================================================

@router.get("/profile", response_model=UserResponse)
async def get_profile(current_user: CurrentUser):
    """Get current user's profile."""
    return current_user


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    user_data: UserUpdate,
    db: DbSession,
    current_user: CurrentUser
):
    """Update current user's profile."""
    # Update fields
    if user_data.first_name is not None:
        current_user.first_name = user_data.first_name
    if user_data.last_name is not None:
        current_user.last_name = user_data.last_name
    if user_data.phone is not None:
        current_user.phone = user_data.phone
    if user_data.marketing_opt_in is not None:
        current_user.marketing_opt_in = user_data.marketing_opt_in
    
    db.commit()
    db.refresh(current_user)
    
    return current_user


@router.post("/change-password", response_model=Message)
async def change_password(
    password_data: PasswordChange,
    db: DbSession,
    current_user: CurrentUser
):
    """Change current user's password."""
    # Verify current password
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contraseña actual incorrecta"
        )
    
    # Update password
    current_user.password_hash = get_password_hash(password_data.new_password)
    db.commit()
    
    return Message(message="Contraseña actualizada correctamente")


@router.put("/preferences", response_model=Message)
async def update_preferences(
    marketing_opt_in: bool,
    db: DbSession,
    current_user: CurrentUser
):
    """Update marketing preferences."""
    current_user.marketing_opt_in = marketing_opt_in
    db.commit()
    
    return Message(message="Preferencias actualizadas")


# =============================================================================
# Address endpoints
# =============================================================================

@router.get("/addresses", response_model=List[AddressResponse])
async def list_addresses(db: DbSession, current_user: CurrentUser):
    """Get current user's addresses."""
    addresses = db.query(Address).filter(
        Address.user_id == current_user.id
    ).order_by(Address.is_default.desc(), Address.created_at.desc()).all()
    
    return addresses


@router.post("/addresses", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
async def create_address(
    address_data: AddressCreate,
    db: DbSession,
    current_user: CurrentUser
):
    """Create a new address."""
    # If this is the default, unset other defaults
    if address_data.is_default:
        db.query(Address).filter(
            Address.user_id == current_user.id,
            Address.is_default == True
        ).update({"is_default": False})
    
    address = Address(
        user_id=current_user.id,
        **address_data.model_dump()
    )
    
    db.add(address)
    db.commit()
    db.refresh(address)
    
    return address


@router.get("/addresses/{address_id}", response_model=AddressResponse)
async def get_address(
    address_id: int,
    db: DbSession,
    current_user: CurrentUser
):
    """Get address by ID."""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dirección no encontrada"
        )
    
    return address


@router.put("/addresses/{address_id}", response_model=AddressResponse)
async def update_address(
    address_id: int,
    address_data: AddressUpdate,
    db: DbSession,
    current_user: CurrentUser
):
    """Update an address."""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dirección no encontrada"
        )
    
    # If setting as default, unset other defaults
    if address_data.is_default:
        db.query(Address).filter(
            Address.user_id == current_user.id,
            Address.is_default == True,
            Address.id != address_id
        ).update({"is_default": False})
    
    # Update fields
    update_data = address_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(address, field, value)
    
    db.commit()
    db.refresh(address)
    
    return address


@router.delete("/addresses/{address_id}", response_model=Message)
async def delete_address(
    address_id: int,
    db: DbSession,
    current_user: CurrentUser
):
    """Delete an address."""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dirección no encontrada"
        )
    
    db.delete(address)
    db.commit()
    
    return Message(message="Dirección eliminada")


@router.post("/addresses/{address_id}/set-default", response_model=AddressResponse)
async def set_default_address(
    address_id: int,
    db: DbSession,
    current_user: CurrentUser
):
    """Set an address as default."""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dirección no encontrada"
        )
    
    # Unset other defaults
    db.query(Address).filter(
        Address.user_id == current_user.id,
        Address.is_default == True
    ).update({"is_default": False})
    
    address.is_default = True
    db.commit()
    db.refresh(address)
    
    return address
