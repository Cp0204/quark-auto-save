from datetime import datetime, timezone, timedelta


def iso_to_cst(iso_time_str: str) -> str:
    """将 ISO 格式的时间字符串转换为 CST(China Standard Time) 时间并格式化为 %Y-%m-%d %H:%M:%S 格式

    Args:
        iso_time_str (str): ISO 格式时间字符串

    Returns:
        str: CST(China Standard Time) 时间字符串
    """
    dt = datetime.fromisoformat(iso_time_str)
    tz = timezone(timedelta(hours=8))
    dt_cst = dt if dt.astimezone(tz) > datetime.now(tz) else dt.astimezone(tz)
    return dt_cst.strftime("%Y-%m-%d %H:%M:%S") if dt_cst.year >= 1970 else ""
