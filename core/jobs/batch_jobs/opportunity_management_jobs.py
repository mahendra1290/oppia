# coding: utf-8
#
# Copyright 2021 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Jobs that manage Exploration Opportunity models."""

from __future__ import annotations

import itertools

from core.domain import exp_domain
from core.domain import exp_fetchers
from core.domain import opportunity_services
from core.domain import story_domain
from core.domain import story_fetchers
from core.domain import topic_domain
from core.domain import topic_fetchers
from core.jobs import base_jobs
from core.jobs.io import ndb_io
from core.jobs.types import job_run_result
from core.platform import models

import apache_beam as beam

from typing import Dict, List, Union

MYPY = False
if MYPY: # pragma: no cover
    from mypy_imports import exp_models
    from mypy_imports import opportunity_models
    from mypy_imports import story_models
    from mypy_imports import topic_models

(
    exp_models, opportunity_models, story_models,
    topic_models
) = models.Registry.import_models([
    models.NAMES.exploration, models.NAMES.opportunity, models.NAMES.story,
    models.NAMES.topic
])


class DeleteExplorationOpportunitySummariesJob(base_jobs.JobBase):
    """Job that deletes ExplorationOpportunitySummaryModels."""

    def run(self) -> beam.PCollection[job_run_result.JobRunResult]:
        """Returns a PCollection of 'SUCCESS' or 'FAILURE' results from
        deleting ExplorationOpportunitySummaryModel.

        Returns:
            PCollection. A PCollection of 'SUCCESS' or 'FAILURE' results from
            deleting ExplorationOpportunitySummaryModel.
        """
        exp_opportunity_summary_model = (
            self.pipeline
            | 'Get all non-deleted opportunity models' >> ndb_io.GetModels(
                opportunity_models.ExplorationOpportunitySummaryModel.get_all(
                    include_deleted=False))
        )

        unused_delete_result = (
            exp_opportunity_summary_model
            | beam.Map(lambda model: model.key)
            | 'Delete all models' >> ndb_io.DeleteModels()
        )

        return (
            exp_opportunity_summary_model
            | 'Count all new models' >> beam.combiners.Count.Globally()
            | 'Only create result for new models when > 0' >> (
                beam.Filter(lambda n: n > 0))
            | 'Create result for new models' >> beam.Map(
                lambda n: job_run_result.JobRunResult(stdout='SUCCESS %s' % n))
        )


class GenerateExplorationOpportunitySummariesJob(base_jobs.JobBase):
    """Job that regenerates ExplorationOpportunitySummaryModel.

    NOTE: The DeleteExplorationOpportunitySummariesJob must be run before this
    job.
    """

    @staticmethod
    def _generate_opportunities_related_to_topic(
        topic: topic_domain.Topic,
        stories_dict: Dict[str, story_domain.Story],
        exps_dict: Dict[str, exp_domain.Exploration]
    ) -> Dict[str, Union[
        str,
        job_run_result.JobRunResult,
        List[opportunity_models.ExplorationOpportunitySummaryModel]]
    ]:
        """Generate opportunities related to a topic.

        Args:
            topic: Topic. Topic for which to generate the opportunities.
            stories_dict: dict(str, Story). All stories in the datastore, keyed
                by their ID.
            exps_dict: dict(str, Exploration). All explorations in
                the datastore, keyed by their ID.

        Returns:
            dict(str, *). Metadata about the operation. Keys are:
                status: str. Whether the job succeeded or failed.
                job_result: JobRunResult. A detailed report of the status,
                    including exception details if a failure occurred.
                models: list(ExplorationOpportunitySummaryModel). The models
                    generated by the operation.
        """
        try:
            story_ids = topic.get_canonical_story_ids() # type: ignore[no-untyped-call]
            existing_story_ids = (
                set(stories_dict.keys()).intersection(story_ids))
            exp_ids: List[str] = list(itertools.chain.from_iterable(
                stories_dict[story_id].story_contents.get_all_linked_exp_ids()
                for story_id in existing_story_ids))
            existing_exp_ids = set(exps_dict.keys()).intersection(exp_ids)

            missing_story_ids = set(story_ids).difference(existing_story_ids)
            missing_exp_ids = set(exp_ids).difference(existing_exp_ids)
            if len(missing_exp_ids) > 0 or len(missing_story_ids) > 0:
                raise Exception(
                    'Failed to regenerate opportunities for topic id: %s, '
                    'missing_exp_with_ids: %s, missing_story_with_ids: %s' % (
                        topic.id,
                        list(missing_exp_ids),
                        list(missing_story_ids)))

            exploration_opportunity_summary_list = []
            stories = [
                stories_dict[story_id] for story_id in existing_story_ids
            ]
            for story in stories:
                for exp_id in story.story_contents.get_all_linked_exp_ids():
                    exploration_opportunity_summary_list.append(
                        opportunity_services.create_exp_opportunity_summary( # type: ignore[no-untyped-call]
                            topic, story, exps_dict[exp_id]))

            exploration_opportunity_summary_model_list = []
            for opportunity in exploration_opportunity_summary_list:
                model = opportunity_models.ExplorationOpportunitySummaryModel(
                    id=opportunity.id,
                    topic_id=opportunity.topic_id,
                    topic_name=opportunity.topic_name,
                    story_id=opportunity.story_id,
                    story_title=opportunity.story_title,
                    chapter_title=opportunity.chapter_title,
                    content_count=opportunity.content_count,
                    incomplete_translation_language_codes=(
                        opportunity.incomplete_translation_language_codes),
                    translation_counts=opportunity.translation_counts,
                    language_codes_needing_voice_artists=(
                        opportunity.language_codes_needing_voice_artists),
                    language_codes_with_assigned_voice_artists=(
                        opportunity.language_codes_with_assigned_voice_artists))
                model.update_timestamps()
                exploration_opportunity_summary_model_list.append(model)

            return {
                'status': 'SUCCESS',
                'job_result': job_run_result.JobRunResult(stdout='SUCCESS'),
                'models': exploration_opportunity_summary_model_list
            }
        except Exception as e:
            return {
                'status': 'FAILURE',
                'job_result': job_run_result.JobRunResult(
                    stderr='FAILURE: %s' % e),
                'models': []
            }

    def run(self) -> beam.PCollection[job_run_result.JobRunResult]:
        """Returns a PCollection of 'SUCCESS' or 'FAILURE' results from
        generating ExplorationOpportunitySummaryModel.

        Returns:
            PCollection. A PCollection of 'SUCCESS' or 'FAILURE' results from
            generating ExplorationOpportunitySummaryModel.
        """

        topics = (
            self.pipeline
            | 'Get all non-deleted topic models' >> (
                ndb_io.GetModels(
                    topic_models.TopicModel.get_all(include_deleted=False)))
            | 'Get topic from model' >> beam.Map(
                topic_fetchers.get_topic_from_model)
        )

        story_ids_to_story = (
            self.pipeline
            | 'Get all non-deleted story models' >> ndb_io.GetModels(
                story_models.StoryModel.get_all(include_deleted=False))
            | 'Get story from model' >> beam.Map(
                story_fetchers.get_story_from_model)
            | 'Combine stories and ids' >> beam.Map(
                lambda story: (story.id, story))
        )

        exp_ids_to_exp = (
            self.pipeline
            | 'Get all non-deleted exp models' >> ndb_io.GetModels(
                exp_models.ExplorationModel.get_all(include_deleted=False))
            | 'Get exploration from model' >> beam.Map(
                exp_fetchers.get_exploration_from_model)
            | 'Combine exploration and ids' >> beam.Map(
                lambda exp: (exp.id, exp))
        )

        stories_dict = beam.pvalue.AsDict(story_ids_to_story)
        exps_dict = beam.pvalue.AsDict(exp_ids_to_exp)

        opportunities_results = (
            topics
            | beam.Map(
                self._generate_opportunities_related_to_topic,
                stories_dict=stories_dict,
                exps_dict=exps_dict)
        )

        unused_put_result = (
            opportunities_results
            | 'Filter the results with SUCCESS status' >> beam.Filter(
                lambda result: result['status'] == 'SUCCESS')
            | 'Fetch the models to be put' >> beam.FlatMap(
                lambda result: result['models'])
            | 'Put models into the datastore' >> ndb_io.PutModels()
        )

        return (
            opportunities_results
            | 'Fetch the job results' >> beam.Map(
                lambda result: result['job_result'])
        )
