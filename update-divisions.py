from pathlib import Path
p=Path('backend/app/models.py'); s=p.read_text(); s=s.replace('    estimation_engineer = "estimation_engineer"','    estimation_engineer = "estimation_engineer"\n    fire_fighting_engineer = "fire_fighting_engineer"\n    elv_engineer = "elv_engineer"'); pos=s.index('class ProjectStatus('); s=s[:pos]+'''class DivisionProject(Base):
    __tablename__ = "division_projects"
    __table_args__ = (UniqueConstraint("division", "reference", name="uq_division_project_reference"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    division: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now, nullable=False)


'''+s[pos:]; p.write_text(s)
p=Path('backend/app/deps.py'); s=p.read_text().replace('# Estimation currently has only its project area and self-service account.', '# These divisions currently have only their project area and self-service account.'); s=s.replace('    if user.role == RoleEnum.estimation_engineer and not (','    area = {RoleEnum.estimation_engineer: "estimation", RoleEnum.fire_fighting_engineer: "fire-fighting", RoleEnum.elv_engineer: "elv"}.get(user.role)\n    if area and not (').replace('path == "/estimation/projects" or path.startswith("/estimation/projects/")','path == f"/{area}/projects" or path.startswith(f"/{area}/projects/")').replace('the estimation team','your division'); p.write_text(s)
p=Path('backend/app/main.py'); s=p.read_text().replace('    estimation,','    estimation,\n    divisions,').replace('app.include_router(estimation.router)','app.include_router(estimation.router)\napp.include_router(divisions.router)'); p.write_text(s)
p=Path('frontend/src/lib/types.ts'); s=p.read_text().replace('| "estimation_engineer"', '| "estimation_engineer" | "fire_fighting_engineer" | "elv_engineer"',1).replace('  estimation_engineer: "Estimation Engineer",','  estimation_engineer: "Estimation Engineer",\n  fire_fighting_engineer: "Fire Fighting Engineer",\n  elv_engineer: "ELV Engineer",'); p.write_text(s)
p=Path('frontend/src/pages/AdminUsersPage.tsx'); s=p.read_text().replace('"viewer", "estimation_engineer"]','"viewer", "estimation_engineer", "fire_fighting_engineer", "elv_engineer"]'); p.write_text(s)
