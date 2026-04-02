from __future__ import annotations

from typing import Any

from .DebugArtifactBuilder import DebugArtifactBuilder
from .MapImportBuilder import MapImportBuilder
from .PageProcessor import PageProcessor
from .TranslatorOverrides import TranslatorOverrides
from ..configuration.TranslatorConfig import TranslatorConfig
from ..documents.SourceDocument import SourceDocument
from ..documents.SourceDocumentLoader import SourceDocumentLoader
from ..importers import import_map_to_backend
from ..inference.NameInferenceService import NameInferenceService
from ..inference.OcrTextExtractor import OcrTextExtractor
from ..inference.ScaleInferenceService import ScaleInferenceService
from ..inference.SpaceTypeClassifier import SpaceTypeClassifier
from ..results.TranslationResult import TranslationResult


class FloorPlanTranslator:
    def __init__(self, config: TranslatorConfig) -> None:
        self.config = config
        self.document_loader = SourceDocumentLoader(config)
        self.name_inference = NameInferenceService(config)
        self.text_extractor = OcrTextExtractor(config)
        self.scale_inference = ScaleInferenceService(config, self.text_extractor)
        self.space_classifier = SpaceTypeClassifier(config)
        self.debug_builder = DebugArtifactBuilder()
        self.page_processor = PageProcessor(
            config=config,
            name_inference=self.name_inference,
            text_extractor=self.text_extractor,
            scale_inference=self.scale_inference,
            space_classifier=self.space_classifier,
            debug_builder=self.debug_builder,
        )
        self.map_import_builder = MapImportBuilder(config)

    def translate_path(self, source_path: str, overrides: TranslatorOverrides | None = None) -> dict[str, Any]:
        document = self.document_loader.load_from_path(source_path)
        return self.translate_document(document, overrides=overrides)

    def translate_bytes(
        self,
        payload: bytes,
        source_name: str,
        overrides: TranslatorOverrides | None = None,
    ) -> dict[str, Any]:
        document = self.document_loader.load_from_bytes(payload, source_name)
        return self.translate_document(document, overrides=overrides)

    def translate_document(
        self,
        document: SourceDocument,
        overrides: TranslatorOverrides | None = None,
    ) -> dict[str, Any]:
        overrides = (overrides or TranslatorOverrides()).apply_mode_defaults()
        campus_name, warnings = self.name_inference.infer_campus_name(
            source_name=document.source_name,
            metadata=document.metadata,
            explicit_name=overrides.campus_name,
        )
        if campus_name is None:
            raise ValueError(warnings[-1] if warnings else "Campus name is required.")

        building_name = self.name_inference.infer_building_name(
            campus_name=campus_name,
            source_name=document.source_name,
            metadata=document.metadata,
            explicit_name=overrides.building_name,
        )
        campus_id = overrides.campus_id or self.name_inference.slug_id(campus_name, prefix="campus")
        building_id = overrides.building_id or self.name_inference.slug_id(building_name, prefix="building")

        analyzed_floors = [
            self.page_processor.analyze_page(
                page=page,
                document=document,
                overrides=overrides,
                building_id=building_id,
            )
            for page in document.pages
        ]
        map_import = self.map_import_builder.build(
            campus_id=campus_id,
            campus_name=campus_name,
            building_id=building_id,
            building_name=building_name,
            floors=analyzed_floors,
        )
        backend_import = None
        if overrides.import_to_backend:
            base_url = overrides.backend_import_base_url or self.config.project.backend_import_base_url
            if not base_url:
                raise ValueError(
                    "import_to_backend was requested, but no backend_import_base_url is configured. "
                    "Point it at the spatial backend, for example http://localhost:8001/api/v1, "
                    "or set import_to_backend to false if you only want the translated JSON."
                )
            backend_import = import_map_to_backend(map_import, base_url)

        result = TranslationResult(
            source={
                "name": document.source_name,
                "type": document.source_type,
                "path": document.source_path,
                "page_count": len(document.pages),
                "metadata": document.metadata,
            },
            warnings=warnings + [warning for floor in analyzed_floors for warning in floor.warnings],
            campus={
                "id": campus_id,
                "display_name": campus_name,
                "name_inferred": overrides.campus_name is None,
            },
            building={
                "id": building_id,
                "display_name": building_name,
                "name_inferred": overrides.building_name is None,
            },
            floors=analyzed_floors,
            map_import=map_import if self.config.export.emit_backend_map else None,
            backend_import=backend_import,
        )
        return result.to_dict()
