#!/usr/bin/env python3
"""
PaperFit 配置验证器

验证所有 YAML 配置文件的完整性和一致性。
防止配置错误导致检测失效或 Agent 行为异常。

用法:
    python config_validator.py [--verbose]
"""

import os
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from typing import TypedDict

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


# ============================================================
# 配置 Schema 定义
# ============================================================

CONFIG_DIR = Path(__file__).parent.parent / "config"
TEMPLATE_SCHEMA_PATH = CONFIG_DIR / "templates.schema.json"

CONFIG_FILES = {
    "vto_taxonomy.yaml": "VTO 缺陷分类体系",
    "layout_rules.yaml": "版式硬规则与阈值",
    "writing_rules.yaml": "写作硬规则",
    "templates.yaml": "模板参数",
    "template_registry_seed.yaml": "模板资产种子注册表",
    "agent_roles.yaml": "Agent 职责描述",
}


@dataclass
class ValidationError:
    """验证错误"""
    file: str
    field: str
    message: str
    severity: str  # "error" | "warning"


class LayoutRulesSchema(TypedDict, total=False):
    """layout_rules.yaml 的 schema"""
    whitespace: Dict[str, float]
    table: Dict[str, Any]
    float: Dict[str, Any]
    equation: Dict[str, float]
    paragraph: Dict[str, Any]
    font: Dict[str, Any]
    consistency: Dict[str, bool]


class VtoTaxonomySchema(TypedDict, total=False):
    """vto_taxonomy.yaml 的 schema"""
    version: str
    description: str
    categories: List[Dict[str, Any]]
    defects: List[Dict[str, Any]]
    severity_levels: Dict[str, Any]
    skill_routing: Dict[str, List[str]]


# ============================================================
# 验证器类
# ============================================================

class ConfigValidator:
    """配置验证器"""

    def __init__(self, config_dir: Path = CONFIG_DIR):
        self.config_dir = config_dir
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []
        self.loaded_configs: Dict[str, Any] = {}

    def validate_all(self) -> Tuple[bool, List[ValidationError], List[ValidationError]]:
        """验证所有配置文件"""
        print(f"验证配置目录：{self.config_dir}")
        print("-" * 50)

        for config_file, description in CONFIG_FILES.items():
            config_path = self.config_dir / config_file
            if not config_path.exists():
                self.errors.append(ValidationError(
                    file=config_file,
                    field="",
                    message=f"配置文件不存在：{config_file}",
                    severity="error"
                ))
                print(f"[缺失] {config_file}: {description}")
                continue

            print(f"[检查] {config_file}: {description}")
            self._load_and_validate(config_file)

        print("-" * 50)

        # 输出错误和警告
        if self.errors:
            print(f"\n发现 {len(self.errors)} 个错误:")
            for err in self.errors:
                print(f"  [ERROR] {err.file}: {err.field} - {err.message}")

        if self.warnings:
            print(f"\n发现 {len(self.warnings)} 个警告:")
            for warn in self.warnings:
                print(f"  [WARN] {warn.file}: {warn.field} - {warn.message}")

        success = len(self.errors) == 0
        print(f"\n验证结果：{'通过' if success else '失败'} ({len(self.warnings)} 个警告)")

        return success, self.errors, self.warnings

    def _load_and_validate(self, config_file: str) -> None:
        """加载并验证单个配置文件"""
        config_path = self.config_dir / config_file

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            self.loaded_configs[config_file] = config
        except yaml.YAMLError as e:
            self.errors.append(ValidationError(
                file=config_file,
                field="",
                message=f"YAML 解析错误：{e}",
                severity="error"
            ))
            return
        except Exception as e:
            self.errors.append(ValidationError(
                file=config_file,
                field="",
                message=f"读取失败：{e}",
                severity="error"
            ))
            return

        # 根据文件类型进行特定验证
        if config_file == "layout_rules.yaml":
            self._validate_layout_rules(config_file, config)
        elif config_file == "vto_taxonomy.yaml":
            self._validate_vto_taxonomy(config_file, config)
        elif config_file == "templates.yaml":
            self._validate_templates(config_file, config)
        elif config_file == "template_registry_seed.yaml":
            self._validate_template_registry(config_file, config)
        elif config_file == "writing_rules.yaml":
            self._validate_writing_rules(config_file, config)
        elif config_file == "agent_roles.yaml":
            self._validate_agent_roles(config_file, config)

    def _validate_layout_rules(self, file: str, config: Dict) -> None:
        """验证 layout_rules.yaml"""
        # 验证 whitespace 部分
        whitespace = config.get('whitespace', {})
        if 'trailing_whitespace_max_ratio' in whitespace:
            val = whitespace['trailing_whitespace_max_ratio']
            if not (0 < val < 1):
                self.errors.append(ValidationError(
                    file=file, field="whitespace.trailing_whitespace_max_ratio",
                    message=f"阈值必须在 0-1 之间，当前值：{val}", severity="error"
                ))

        # 验证 table 部分
        table = config.get('table', {})
        if 'min_width_utilization' in table and 'max_width_utilization' in table:
            if table['min_width_utilization'] >= table['max_width_utilization']:
                self.errors.append(ValidationError(
                    file=file, field="table.width_utilization",
                    message="min_width_utilization 必须小于 max_width_utilization",
                    severity="error"
                ))

        # 验证 float 部分
        float_cfg = config.get('float', {})
        if 'max_reference_distance_pages' in float_cfg:
            val = float_cfg['max_reference_distance_pages']
            if val < 0:
                self.errors.append(ValidationError(
                    file=file, field="float.max_reference_distance_pages",
                    message=f"距离不能为负数，当前值：{val}", severity="error"
                ))

    def _validate_vto_taxonomy(self, file: str, config: Dict) -> None:
        """验证 vto_taxonomy.yaml"""
        # 验证 categories
        categories = config.get('categories', [])
        category_ids = set()
        for cat in categories:
            cat_id = cat.get('id')
            if not cat_id:
                self.errors.append(ValidationError(
                    file=file, field="categories[].id",
                    message="分类缺少 id 字段", severity="error"
                ))
                continue
            if cat_id in category_ids:
                self.errors.append(ValidationError(
                    file=file, field="categories[].id",
                    message=f"分类 ID 重复：{cat_id}", severity="error"
                ))
            category_ids.add(cat_id)

        # 验证 defects
        defects = config.get('defects', [])
        defect_ids = set()
        for defect in defects:
            defect_id = defect.get('id')
            if not defect_id:
                self.errors.append(ValidationError(
                    file=file, field="defects[].id",
                    message="缺陷缺少 id 字段", severity="error"
                ))
                continue
            if defect_id in defect_ids:
                self.errors.append(ValidationError(
                    file=file, field="defects[].id",
                    message=f"缺陷 ID 重复：{defect_id}", severity="error"
                ))
            defect_ids.add(defect_id)

            # 验证 category 引用
            category = defect.get('category')
            if category and category not in category_ids:
                self.errors.append(ValidationError(
                    file=file, field=f"defects[{defect_id}].category",
                    message=f"引用不存在的分类：{category}", severity="error"
                ))

            # 验证 skill_routing
            skill_routing = defect.get('fix_strategy', {}).get('primary_skill')
            if skill_routing:
                valid_skills = [
                    'space-util-fixer', 'float-optimizer', 'consistency-polisher',
                    'overflow-repair', 'template-migrator', 'visual-inspector', 'writing-polish'
                ]
                if skill_routing not in valid_skills:
                    self.warnings.append(ValidationError(
                        file=file, field=f"defects[{defect_id}].fix_strategy.primary_skill",
                        message=f"未知的技能：{skill_routing}", severity="warning"
                    ))

        # 验证 skill_routing 一致性
        skill_routing_cfg = config.get('skill_routing', {})
        for skill, defect_list in skill_routing_cfg.items():
            for defect_ref in defect_list:
                if defect_ref not in defect_ids:
                    self.errors.append(ValidationError(
                        file=file, field=f"skill_routing.{skill}",
                        message=f"引用不存在的缺陷 ID: {defect_ref}", severity="error"
                    ))

    def _validate_templates(self, file: str, config: Dict) -> None:
        """验证 templates.yaml"""
        if jsonschema is None:
            self.warnings.append(ValidationError(
                file=file, field="",
                message="jsonschema 未安装，跳过 templates.schema.json 校验",
                severity="warning"
            ))
        elif TEMPLATE_SCHEMA_PATH.exists():
            try:
                schema = yaml.safe_load(TEMPLATE_SCHEMA_PATH.read_text(encoding="utf-8"))
                jsonschema.validate(config, schema)
            except jsonschema.ValidationError as exc:
                path = ".".join(str(part) for part in exc.path)
                self.errors.append(ValidationError(
                    file=file, field=path or "",
                    message=f"Schema 校验失败：{exc.message}", severity="error"
                ))
            except Exception as exc:
                self.errors.append(ValidationError(
                    file=file, field="",
                    message=f"读取或应用 templates.schema.json 失败：{exc}", severity="error"
                ))

        templates = config.get('templates', {})
        for name, template in templates.items():
            if 'columns' in template:
                self.warnings.append(ValidationError(
                    file=file, field=f"templates.{name}.columns",
                    message="旧字段 columns 已过时，请改用 column_type",
                    severity="warning"
                ))
            if 'target_pages' in template:
                self.warnings.append(ValidationError(
                    file=file, field=f"templates.{name}.target_pages",
                    message="旧字段 target_pages 已过时，请改用 expected_pages.main_body",
                    severity="warning"
                ))
            column_type = template.get("column_type")
            if column_type not in ("single", "double"):
                self.errors.append(ValidationError(
                    file=file, field=f"templates.{name}.column_type",
                    message=f"column_type 必须是 single 或 double，当前值：{column_type}",
                    severity="error"
                ))
            expected_pages = template.get("expected_pages")
            if isinstance(expected_pages, dict):
                for scope_name, scope_value in expected_pages.items():
                    if scope_value is not None and int(scope_value) < 1:
                        self.errors.append(ValidationError(
                            file=file, field=f"templates.{name}.expected_pages.{scope_name}",
                            message=f"页数必须大于 0，当前值：{scope_value}", severity="error"
                        ))

    def _validate_template_registry(self, file: str, config: Dict) -> None:
        """验证 template_registry_seed.yaml"""
        assets = config.get("assets")
        venues = config.get("venues")
        if not isinstance(assets, dict):
            self.errors.append(ValidationError(
                file=file, field="assets",
                message="assets 必须是对象映射", severity="error"
            ))
            assets = {}
        if not isinstance(venues, dict):
            self.errors.append(ValidationError(
                file=file, field="venues",
                message="venues 必须是对象映射", severity="error"
            ))
            venues = {}

        for asset_id, asset in assets.items():
            if not isinstance(asset, dict):
                self.errors.append(ValidationError(
                    file=file, field=f"assets.{asset_id}",
                    message="asset 条目必须是对象", severity="error"
                ))
                continue
            local_path = asset.get("local_path")
            if local_path:
                resolved = Path(local_path)
                if not resolved.is_absolute():
                    candidates = [
                        self.config_dir.parent / local_path,
                        self.config_dir.parent.parent / local_path,
                    ]
                    resolved = next((c for c in candidates if c.exists()), candidates[0])
                if not resolved.exists():
                    self.errors.append(ValidationError(
                        file=file, field=f"assets.{asset_id}.local_path",
                        message=f"本地模板资产不存在：{resolved}", severity="error"
                    ))
            else:
                self.warnings.append(ValidationError(
                    file=file, field=f"assets.{asset_id}.local_path",
                    message="未声明本地模板资产路径", severity="warning"
                ))

        for venue_id, venue in venues.items():
            if not isinstance(venue, dict):
                self.errors.append(ValidationError(
                    file=file, field=f"venues.{venue_id}",
                    message="venue 条目必须是对象", severity="error"
                ))
                continue
            asset_ref = venue.get("asset_ref")
            if asset_ref and asset_ref not in assets:
                self.errors.append(ValidationError(
                    file=file, field=f"venues.{venue_id}.asset_ref",
                    message=f"引用了不存在的 asset_ref：{asset_ref}", severity="error"
                ))
            status = venue.get("status")
            if status not in ("downloaded", "shared_asset", "partial", "unresolved"):
                self.warnings.append(ValidationError(
                    file=file, field=f"venues.{venue_id}.status",
                    message=f"未识别的 venue 状态：{status}", severity="warning"
                ))

    def _validate_writing_rules(self, file: str, config: Dict) -> None:
        """验证 writing_rules.yaml"""
        # 验证写作规则的基本结构
        if 'forbidden_words' in config:
            if not isinstance(config['forbidden_words'], list):
                self.errors.append(ValidationError(
                    file=file, field="forbidden_words",
                    message="必须是列表类型", severity="error"
                ))

        if 'required_tense' in config:
            tense = config['required_tense']
            valid_tenses = ['present', 'past', 'present_perfect']
            if tense not in valid_tenses:
                self.errors.append(ValidationError(
                    file=file, field="required_tense",
                    message=f"无效的时态：{tense}，有效值：{valid_tenses}",
                    severity="error"
                ))

    def _validate_agent_roles(self, file: str, config: Dict) -> None:
        """验证 agent_roles.yaml"""
        agents = config.get('agents', {})
        for name, role in agents.items():
            if 'description' not in role:
                self.warnings.append(ValidationError(
                    file=file, field=f"agents.{name}",
                    message="缺少 description 字段", severity="warning"
                ))
            if 'tools' not in role:
                self.warnings.append(ValidationError(
                    file=file, field=f"agents.{name}",
                    message="缺少 tools 字段", severity="warning"
                ))


# ============================================================
# 主函数
# ============================================================

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="PaperFit 配置验证器")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='显示详细输出')
    parser.add_argument('--config-dir', type=str, default=None,
                        help='配置文件目录')
    args = parser.parse_args()

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR

    validator = ConfigValidator(config_dir)
    success, errors, warnings = validator.validate_all()

    if args.verbose:
        print("\n已加载的配置:")
        for file, config in validator.loaded_configs.items():
            print(f"  - {file}: {len(str(config))} 字符")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
