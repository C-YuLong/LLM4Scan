"""Freeze a stricter UART target-cell task requiring preservation of data enable.

This reuses the pinned UART, not a new independent IP or an unseen design.
Reference edits remain isolated from the model's input directory.
"""
import json
from pathlib import Path
import shutil


def main():
    suite = Path(__file__).resolve().parents[3] / 'benchmarks/opensource_20261001'
    source = suite / 'cases/P06_uart_plain/input'
    oracle = suite / 'oracles/P06_uart_plain'
    if source.exists() or oracle.exists():
        raise ValueError('Preserve frozen UART target-cell inputs and reference')
    prior = suite / 'cases/P01_uart_enabled_v2/input'
    reference = suite / 'oracles/P01_uart_enabled_v2'
    task = (prior / 'task_spec.md').read_text(encoding='utf-8')
    task = task.replace('# UART 扫描插入与修复', '# UART 指定目标单元的等价扫描修复')
    task = task.replace('uart_si%d', 'uart_plain_si%d').replace('uart_so%d', 'uart_plain_so%d')
    task += '''
## 本题的目标单元限制

原 sky130_fd_sc_hd__dfxtp_1 和 sky130_fd_sc_hd__edfxtp_1 实例的指定扫描目标均为 sky130_fd_sc_hd__sdfxtp_1，不允许使用 sedfxtp_1 或其他扫描单元类型替代该要求。
对于带 DE 的原始寄存器，功能模式下 DE=0 的数据保持行为和 DE=1 的更新行为都必须保持；不得固定 DE 或普通功能输入，也不得仅丢弃 DE 引脚。
若直接映射不能满足这些要求，可以等价修改 Pre-scan 工作副本；原始寄存器实例名保留，以便对应实际链成员和原始数据状态。允许新增实现等价功能所必需的组合逻辑。
普通移位寄存器的非首级 DFF 可以由工具合法保留原类型，但全部原有 79 个寄存器仍须实际参与扫描。其他功能行为不变；原始输入到最终使用的 Pre-scan 必须通过实际 Yosys EQY，无额外输入约束。
'''
    shutil.copytree(prior, source)
    (source / 'task_spec.md').write_text(task, encoding='utf-8')
    original = (source / 'original.dofile').read_text(encoding='utf-8')
    (source / 'original.dofile').write_text(original.replace('uart_si%d', 'uart_plain_si%d').replace('uart_so%d', 'uart_plain_so%d'), encoding='utf-8')
    oracle.mkdir(parents=True)
    correct = (reference / 'correct.dofile').read_text(encoding='utf-8')
    (oracle / 'correct.dofile').write_text(correct.replace('uart_si%d', 'uart_plain_si%d').replace('uart_so%d', 'uart_plain_so%d'), encoding='utf-8')
    shutil.copyfile(reference / 'edits.json', oracle / 'edits.json')
    (oracle / 'scope.json').write_text(json.dumps({
        'source_case': 'P01_uart_enabled_v2', 'same_original_design': True,
        'faults': ['loss_of_data_enable', 'scan_enable_polarity', 'chain_count', 'max_length', 'mix_clocks', 'missing_ctl'],
        'scope': 'New target-cell restriction on an existing pinned UART; not a new IP or hidden-test estimate.'}, indent=2), encoding='utf-8')
    print(json.dumps({'frozen': str(source), 'original_ff': 79, 'enabled_ff': 17, 'faults': 6}))


if __name__ == '__main__':
    main()
