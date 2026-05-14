"""
cluster_tag 동의어 정규화
==========================
AI가 같은 의미를 변형해서 만든 태그들을 canonical 형태로 통합.
- earnings_surprise / earnings_growth / earnings_recovery → earnings_surprise
- osteoarthritis_drug_approval / osteoarthritis_drug → osteoarthritis_drug
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from state_manager import load_state, save_state


# canonical 그룹 — 같은 의미로 합칠 태그들
CANONICAL_MAP = {
    # 실적 어닝 시리즈 → earnings_surprise
    'earnings_surprise': ['earnings_surprise', 'earnings_growth', 'earnings_recovery',
                          'earnings_improvement', 'earnings_growth_q1',
                          'earnings_turnaround', 'profit_growth', 'turnaround_q1',
                          'q1_earnings', 'earnings_q1', 'earnings_growth_2023',
                          'business_turnaround', 'operating_profit_growth',
                          'earnings_performance', 'earnings_boost', 'biotech_earnings',
                          'biotech_growth_hormones'],
    # 바이오 신약 — 적응증별로 분기되긴 하지만 통합
    'osteoarthritis_drug': ['osteoarthritis_drug', 'osteoarthritis_drug_approval', 'osteoarthritis_treatment'],
    # 세포치료제 / NK / CAR-T 통합
    'cell_therapy': ['cell_therapy', 'cell_therapy_commercialization', 'nk_cell_therapy',
                     'car_t_therapy', 'car_t_cancer', 'immunotherapy'],
    # 한타바이러스
    'hantavirus_diagnostics': ['hantavirus_diagnostics', 'hantavirus_response',
                                'hantavirus_diagnostic_kit', 'hantavirus_biotech',
                                'hantavirus_related_bio'],
    # 휴머노이드/로보틱스 — 자동차 로봇/비전/협동 다 통합
    'humanoid_robotics': ['humanoid_robotics', 'humanoid_robot', 'humanoid_partnership',
                          'robotics_partnership', 'collaborative_robotics',
                          'humanoid_robot_partnership', 'automotive_robotics',
                          'automotive_robot', 'robotics_expectations', 'robotics_growth',
                          'robotics_vision', 'policy_innovation'],
    # 반도체 후공정 (테스트/패키지/세정/장비)
    'semiconductor_backend': ['semiconductor_backend', 'semiconductor_testing',
                              'memory_wafer_testing', 'semiconductor_packaging',
                              'semiconductor_collaboration', 'semiconductor_rumor',
                              'semiconductor_rumors', 'semiconductor_cleaning',
                              'semiconductor_tools_growth', 'semiconductor_contracts',
                              'ai_semiconductor', 'ai_semiconductor_materials',
                              'ai_semiconductor_expansion', 'neuromorphic_semiconductors',
                              'micro_display_rumor', 'memory_hbm', 'hbm_memory', 'dram_memory'],
    # HBM/메모리
    'memory_hbm': ['memory_hbm', 'hbm_memory', 'dram_memory'],
    # AI 인프라/데이터센터
    'ai_infrastructure': ['ai_infrastructure', 'ai_infrastructure_policy',
                          'ai_datacenter', 'ondevice_ai', 'on_device_ai',
                          'ondevice_ai_datacenter'],
    # AI 반도체 소재/장비
    'ai_semiconductor': ['ai_semiconductor', 'ai_semiconductor_materials',
                         'ai_chip', 'ai_chip_materials'],
    # 부동산 재개발/재건축
    'realestate_redevelopment': ['realestate_redevelopment', 'realestate_renovation',
                                  'urban_redevelopment', 'redevelopment_expectation',
                                  'policy_redevelopment', 'property_redevelopment',
                                  'realestate_construction', 'local_development_policy',
                                  'urban_development_expectation', 'land_asset_play'],
    # 부동산 분양
    'realestate_sales': ['realestate_sales', 'apartment_sales'],
    # M&A
    'm_and_a': ['m_and_a', 'merger_acquisition', 'merger', 'acquisition', 'corporate_merger',
                'autonomous_vehicle_m_a', 'airline_merger_policy'],
    # 유상증자/CB (희석)
    'dilutive_finance': ['dilutive_finance', 'capital_increase', 'rights_offering',
                          'convertible_bond', 'cb_issuance', 'paid_in_capital_increase',
                          'convertible_bond_impact'],
    # 풍문/테마성
    'momentum_speculation': ['momentum_speculation', 'thematic_speculation',
                              'speculation', 'lithium_supply_rumor'],
    # 우선주 수급
    'preferred_stock_interest': ['preferred_stock_interest', 'preferred_stock'],
    # 태양광 정책
    'solar_energy_policy': ['solar_energy_policy', 'solar_energy', 'solar_export'],
    # 송전망/전선
    'power_grid_expansion': ['power_grid_expansion', 'cable_export', 'eco_copper'],
    # 알츠하이머 신약
    'alzheimer_drug': ['alzheimer_drug', 'alzheimer_licensing', 'alzheimer_technology'],
    # 화장품 OEM/실적
    'cosmetics_growth': ['cosmetics_growth', 'cosmetics_expansion', 'cosmetics_export'],
    # 2차전지 / BMS / 양극재
    'battery_components': ['battery_components', 'bms_growth', 'cathode_supply',
                            'lfp_supply', 'lfp_battery', 'battery_supply'],
    # 보안
    'cybersecurity': ['cybersecurity', 'security_product', 'security_focus'],
    # FDA 승인
    'fda_approval': ['fda_approval', 'fda_clearance', 'us_approval'],
    # 정부 정책 수혜
    'policy_beneficiary': ['policy_beneficiary', 'labor_policy_beneficiary',
                            'government_award'],
    # 동물 의약품
    'animal_pharma': ['animal_pharma', 'veterinary'],
    # 식약처/규제 통과
    'regulatory_approval': ['regulatory_approval', 'kfda_approval'],
    # 디지털 트윈
    'digital_twin': ['digital_twin'],
    # 무상증자
    'free_share_issue': ['free_share_issue', 'bonus_issue'],
    # 부동산 가치 (땅 부자)
    'land_asset_play': ['land_asset_play', 'real_estate_asset_value',
                        'urban_development_expectation'],
}


def build_reverse_map():
    """variant → canonical 매핑"""
    out = {}
    for canonical, variants in CANONICAL_MAP.items():
        for v in variants:
            out[v] = canonical
    return out


def main():
    state = load_state()
    sigs = state['signals']

    rev = build_reverse_map()
    before = Counter(s.get('cluster_tag', '-') for s in sigs.values() if s.get('cluster_tag'))

    changed = 0
    for t, s in sigs.items():
        tag = s.get('cluster_tag')
        if not tag: continue
        if tag in rev and rev[tag] != tag:
            s['cluster_tag_orig'] = tag
            s['cluster_tag'] = rev[tag]
            changed += 1

    save_state(state)
    after = Counter(s.get('cluster_tag', '-') for s in sigs.values() if s.get('cluster_tag'))

    print(f'정규화: {changed}개 태그 변경\n')
    print('=== Before (TOP 15) ===')
    for tag, n in before.most_common(15):
        print(f'  {n:>3}건  {tag}')

    print('\n=== After (TOP 15) ===')
    for tag, n in after.most_common(15):
        print(f'  {n:>3}건  {tag}')

    # 매핑 안 된 태그 (정규화 사전 갱신 후보)
    unmapped = [tag for tag, n in after.items() if n == 1 and tag != '-']
    print(f'\n=== 단일 종목 태그 (미매핑 후보 {len(unmapped)}개) ===')
    for tag in sorted(unmapped)[:30]:
        # 그 태그 가진 종목 이름
        owner = next((s['name'] for s in sigs.values() if s.get('cluster_tag') == tag), '?')
        print(f'  {tag:35s}  ({owner})')

    from reporters.report_generator import _write_dashboard_data
    _write_dashboard_data(Path(__file__).parent.parent / 'reports', state)


if __name__ == '__main__':
    main()
