from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 형식: mysql+pymysql://유저이름:비밀번호@IP주소:포트/데이터베이스이름
DATABASE_URL = "mysql+pymysql://myuser:mypassword@127.0.0.1:3306/mydb"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
