"""python -m livetrans.overlay 入口（包化后由本文件承接 -m 执行）。"""
from .pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
