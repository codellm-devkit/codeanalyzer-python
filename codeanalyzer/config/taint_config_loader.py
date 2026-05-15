################################################################################
# Copyright IBM Corporation 2025
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

"""Taint analysis configuration loader.

This module provides functionality to load taint analysis configurations from
YAML or JSON files and merge them with default configurations.
"""

import json
from pathlib import Path
from typing import Optional, Union

import yaml

from codeanalyzer.schema.py_schema import TaintAnalysisConfig
from codeanalyzer.config.taint_config_defaults import get_default_taint_config
from codeanalyzer.utils import logger


class TaintConfigLoader:
    """Loads and merges taint analysis configurations."""
    
    @staticmethod
    def load_config(
        config_path: Optional[Union[str, Path]] = None,
        use_defaults: bool = True
    ) -> TaintAnalysisConfig:
        """Load taint analysis configuration.
        
        Args:
            config_path: Path to custom configuration file (YAML or JSON).
                        If None, only defaults are used.
            use_defaults: Whether to include default sources/sinks/sanitizers.
                         If True, custom config extends defaults.
                         If False, only custom config is used.
            
        Returns:
            TaintAnalysisConfig: Merged configuration
            
        Raises:
            FileNotFoundError: If config_path is provided but file doesn't exist
            ValueError: If file format is unsupported or invalid
        """
        # Start with defaults if requested
        if use_defaults:
            config = get_default_taint_config()
            logger.debug(f"Loaded default taint configuration with {len(config.sources)} sources, "
                        f"{len(config.sinks)} sinks, {len(config.sanitizers)} sanitizers")
        else:
            config = TaintAnalysisConfig()
            logger.debug("Starting with empty taint configuration")
        
        # Load and merge custom configuration
        if config_path:
            custom_config = TaintConfigLoader._load_from_file(config_path)
            config = TaintConfigLoader._merge_configs(config, custom_config)
            logger.info(f"Merged custom configuration from {config_path}")
        
        # Filter out disabled items
        config = TaintConfigLoader._filter_disabled(config)
        
        logger.info(f"Final taint configuration: {len(config.sources)} sources, "
                   f"{len(config.sinks)} sinks, {len(config.sanitizers)} sanitizers")
        
        return config
    
    @staticmethod
    def _load_from_file(config_path: Union[str, Path]) -> TaintAnalysisConfig:
        """Load configuration from YAML or JSON file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            TaintAnalysisConfig: Loaded configuration
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is unsupported or invalid
        """
        path = Path(config_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        logger.debug(f"Loading taint configuration from {path}")
        content = path.read_text()
        
        # Parse based on file extension
        try:
            if path.suffix in ['.yaml', '.yml']:
                data = yaml.safe_load(content)
            elif path.suffix == '.json':
                data = json.loads(content)
            else:
                raise ValueError(
                    f"Unsupported configuration format: {path.suffix}. "
                    f"Supported formats: .yaml, .yml, .json"
                )
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in configuration file: {e}")
        
        # Convert to Pydantic model
        try:
            return TaintAnalysisConfig.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid taint configuration structure: {e}")
    
    @staticmethod
    def _merge_configs(
        base: TaintAnalysisConfig,
        custom: TaintAnalysisConfig
    ) -> TaintAnalysisConfig:
        """Merge custom configuration into base configuration.
        
        Custom sources/sinks/sanitizers are added to the base.
        If a custom item has the same name as a base item, it overrides it.
        
        Args:
            base: Base configuration (typically defaults)
            custom: Custom configuration to merge in
            
        Returns:
            TaintAnalysisConfig: Merged configuration
        """
        # Create name-based lookups for base config
        base_sources = {s.name: s for s in base.sources}
        base_sinks = {s.name: s for s in base.sinks}
        base_sanitizers = {s.name: s for s in base.sanitizers}
        
        # Track what was overridden
        overridden_sources = []
        overridden_sinks = []
        overridden_sanitizers = []
        
        # Merge sources
        for source in custom.sources:
            if source.name in base_sources:
                overridden_sources.append(source.name)
            base_sources[source.name] = source
        
        # Merge sinks
        for sink in custom.sinks:
            if sink.name in base_sinks:
                overridden_sinks.append(sink.name)
            base_sinks[sink.name] = sink
        
        # Merge sanitizers
        for sanitizer in custom.sanitizers:
            if sanitizer.name in base_sanitizers:
                overridden_sanitizers.append(sanitizer.name)
            base_sanitizers[sanitizer.name] = sanitizer
        
        # Log merge information
        if overridden_sources:
            logger.debug(f"Overridden sources: {', '.join(overridden_sources)}")
        if overridden_sinks:
            logger.debug(f"Overridden sinks: {', '.join(overridden_sinks)}")
        if overridden_sanitizers:
            logger.debug(f"Overridden sanitizers: {', '.join(overridden_sanitizers)}")
        
        # Merge exclude lists (combine both)
        merged_exclude_files = list(set(base.exclude_files + custom.exclude_files))
        merged_exclude_functions = list(set(base.exclude_functions + custom.exclude_functions))
        
        # Create merged config
        # Use custom values for options if they differ from defaults
        return TaintAnalysisConfig(
            sources=list(base_sources.values()),
            sinks=list(base_sinks.values()),
            sanitizers=list(base_sanitizers.values()),
            max_path_length=custom.max_path_length if custom.max_path_length != 10 else base.max_path_length,
            include_implicit_flows=custom.include_implicit_flows or base.include_implicit_flows,
            confidence_threshold=custom.confidence_threshold if custom.confidence_threshold != "medium" else base.confidence_threshold,
            exclude_files=merged_exclude_files,
            exclude_functions=merged_exclude_functions,
            include_safe_flows=custom.include_safe_flows or base.include_safe_flows,
            group_by_vulnerability=custom.group_by_vulnerability if not custom.group_by_vulnerability else base.group_by_vulnerability,
        )
    
    @staticmethod
    def _filter_disabled(config: TaintAnalysisConfig) -> TaintAnalysisConfig:
        """Filter out disabled sources, sinks, and sanitizers.
        
        Args:
            config: Configuration to filter
            
        Returns:
            TaintAnalysisConfig: Filtered configuration with only enabled items
        """
        enabled_sources = [s for s in config.sources if s.enabled]
        enabled_sinks = [s for s in config.sinks if s.enabled]
        enabled_sanitizers = [s for s in config.sanitizers if s.enabled]
        
        disabled_count = (
            len(config.sources) - len(enabled_sources) +
            len(config.sinks) - len(enabled_sinks) +
            len(config.sanitizers) - len(enabled_sanitizers)
        )
        
        if disabled_count > 0:
            logger.debug(f"Filtered out {disabled_count} disabled items")
        
        return TaintAnalysisConfig(
            sources=enabled_sources,
            sinks=enabled_sinks,
            sanitizers=enabled_sanitizers,
            max_path_length=config.max_path_length,
            include_implicit_flows=config.include_implicit_flows,
            confidence_threshold=config.confidence_threshold,
            exclude_files=config.exclude_files,
            exclude_functions=config.exclude_functions,
            include_safe_flows=config.include_safe_flows,
            group_by_vulnerability=config.group_by_vulnerability,
        )
    
    @staticmethod
    def save_config(
        config: TaintAnalysisConfig,
        output_path: Union[str, Path],
        format: str = "yaml"
    ) -> None:
        """Save configuration to file.
        
        Args:
            config: Configuration to save
            output_path: Path where to save the configuration
            format: Output format ('yaml' or 'json')
            
        Raises:
            ValueError: If format is unsupported
        """
        path = Path(output_path)
        
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if format.lower() in ['yaml', 'yml']:
            content = yaml.dump(
                config.model_dump(),
                default_flow_style=False,
                sort_keys=False,
                indent=2
            )
        elif format.lower() == 'json':
            content = config.model_dump_json(indent=2)
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'yaml' or 'json'")
        
        path.write_text(content)
        logger.info(f"Saved taint configuration to {path}")
    
    @staticmethod
    def validate_config(config: TaintAnalysisConfig) -> list[str]:
        """Validate configuration and return list of warnings/errors.
        
        Args:
            config: Configuration to validate
            
        Returns:
            list[str]: List of validation issues (empty if valid)
        """
        issues = []
        
        # Check for duplicate names
        source_names = [s.name for s in config.sources]
        if len(source_names) != len(set(source_names)):
            duplicates = [name for name in source_names if source_names.count(name) > 1]
            issues.append(f"Duplicate source names found: {', '.join(set(duplicates))}")
        
        sink_names = [s.name for s in config.sinks]
        if len(sink_names) != len(set(sink_names)):
            duplicates = [name for name in sink_names if sink_names.count(name) > 1]
            issues.append(f"Duplicate sink names found: {', '.join(set(duplicates))}")
        
        sanitizer_names = [s.name for s in config.sanitizers]
        if len(sanitizer_names) != len(set(sanitizer_names)):
            duplicates = [name for name in sanitizer_names if sanitizer_names.count(name) > 1]
            issues.append(f"Duplicate sanitizer names found: {', '.join(set(duplicates))}")
        
        # Validate patterns are not empty
        for source in config.sources:
            if not source.pattern.strip():
                issues.append(f"Empty pattern for source: {source.name}")
        
        for sink in config.sinks:
            if not sink.pattern.strip():
                issues.append(f"Empty pattern for sink: {sink.name}")
        
        for sanitizer in config.sanitizers:
            if not sanitizer.pattern.strip():
                issues.append(f"Empty pattern for sanitizer: {sanitizer.name}")
        
        # Check if there are any sources and sinks
        if not config.sources:
            issues.append("No taint sources configured")
        
        if not config.sinks:
            issues.append("No taint sinks configured")
        
        return issues
