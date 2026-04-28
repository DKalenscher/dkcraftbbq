from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/admin/links", tags=["admin-links"])


@router.post("", response_model=schemas.LinkResponse, status_code=status.HTTP_201_CREATED)
def create_link(
    body: schemas.LinkCreate,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    section = db.query(models.Section).filter(models.Section.id == body.section_id).first()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    max_order = (
        db.query(models.Link)
        .filter(models.Link.section_id == body.section_id)
        .count()
    )
    link = models.Link(
        section_id=body.section_id,
        title=body.title,
        description=body.description,
        url=body.url,
        image_url=body.image_url,
        is_visible=body.is_visible,
        order_index=max_order,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.put("/{link_id}", response_model=schemas.LinkResponse)
def update_link(
    link_id: int,
    body: schemas.LinkUpdate,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    link = db.query(models.Link).filter(models.Link.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")

    if body.section_id is not None:
        section = db.query(models.Section).filter(models.Section.id == body.section_id).first()
        if not section:
            raise HTTPException(status_code=404, detail="Section not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(link, field, value)

    db.commit()
    db.refresh(link)
    return link


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(
    link_id: int,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    link = db.query(models.Link).filter(models.Link.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()


@router.post("/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_links(
    body: schemas.ReorderRequest,
    db: Session = Depends(get_db),
    _: models.AdminUser = Depends(get_current_user),
):
    for idx, link_id in enumerate(body.ordered_ids):
        db.query(models.Link).filter(models.Link.id == link_id).update(
            {"order_index": idx}
        )
    db.commit()
