"""pii.py — PII 마스킹 유틸 (표시 전용). tbm/modules/pii.py 이식.

원칙(공통규칙 8): 저장 데이터에는 원본을 보관하고, **출력 시에만** 마스킹한다.
  · 카드·표·엑셀       → 마스킹
  · 상세 편집 입력 위젯 → 원본 (마스킹하면 편집이 불가능해진다)
"""
from __future__ import annotations


def mask_name(name: str) -> str:
    """이름 마스킹: 첫 글자·끝 글자 유지, 중간만 '*' 처리.

    홍길동 → 홍*동 | 이순신 → 이*신 | Kim → K*m | 이순 → 이*
    그룹이름(콤마 구분) 시 각각 마스킹
    """
    if not name or not isinstance(name, str):
        return name
    parts = [n.strip() for n in name.split(",")]
    masked = []
    for n in parts:
        if len(n) <= 1:
            masked.append(n)
        elif len(n) == 2:
            masked.append(n[0] + "*")
        else:
            masked.append(n[0] + "*" * (len(n) - 2) + n[-1])
    return ", ".join(masked)


def mask_phone(phone: str) -> str:
    """전화번호 마스킹: 가운데 4자리 → '****'. 그룹(콤마 구분) 시 전체 생략 표시."""
    if not phone or not isinstance(phone, str):
        return phone
    if "," in phone:
        return "****"
    parts = str(phone).replace(" ", "").split("-")
    if len(parts) == 3:
        return f"{parts[0]}-****-{parts[2]}"
    if len(phone) >= 10:
        return phone[:3] + "****" + phone[7:]
    return "***-****-****"
