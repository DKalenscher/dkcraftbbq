from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/admin/sections", tags=["admin-sections"])


@router.get("", response_model=list[schemas.SectionWithLinksResponse])
def list_sections(
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    return db.query(models.Section).order_by(models.Section.order_index).all()


@router.post("", response_model=schemas.SectionResponse, status_code=status.HTTP_201_CREATED)
def create_section(
    body: schemas.SectionCreate,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    max_order = db.query(models.Section).count()
    section = models.Section(
        title=body.title,
        description=body.description,
        is_visible=body.is_visible,
        order_index=max_order,
    )
    db.add(section)
    db.commit()
    db.refresh(section)
    return section


@router.put("/{section_id}", response_model=schemas.SectionResponse)
def update_section(
    section_id: int,
    body: schemas.SectionUpdate,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    section = db.query(models.Section).filter(models.Section.id == section_id).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(section, field, value)

    db.commit()
    db.refresh(section)
    return section


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_section(
    section_id: int,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    section = db.query(models.Section).filter(models.Section.id == section_id).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    db.delete(section)
    db.commit()


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_sections(
    body: schemas.ReorderRequest,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    for idx, section_id in enumerate(body.ordered_ids):
        db.query(models.Section).filter(models.Section.id == section_id).update(
            {"order_index": idx}
        )
    db.commit()
