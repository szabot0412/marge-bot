import logging as log
import time

from . import gitlab
from .approvals import Approvals


GET, POST, PUT, DELETE = gitlab.GET, gitlab.POST, gitlab.PUT, gitlab.DELETE


class MergeRequest(gitlab.Resource):

    @classmethod
    def create(cls, api, project_id, params):
        merge_request_info = api.call(POST(
            '/projects/{project_id}/merge_requests'.format(project_id=project_id),
            params,
        ))
        merge_request = cls(api, merge_request_info)
        return merge_request

    @classmethod
    def search(cls, api, project_id, params):
        merge_requests = api.collect_all_pages(GET(
            '/projects/{project_id}/merge_requests'.format(project_id=project_id),
            params,
        ))
        return [cls(api, merge_request) for merge_request in merge_requests]

    @classmethod
    def fetch_by_iid(cls, project_id, merge_request_iid, api):
        merge_request = cls(api, {'iid': merge_request_iid, 'project_id': project_id})
        merge_request.refetch_info()
        return merge_request

    @classmethod
    def fetch_all_open_for_user(cls, project_id, user_id, api, merge_order):
        all_merge_request_infos = api.collect_all_pages(GET(
            '/projects/{project_id}/merge_requests'.format(project_id=project_id),
            {'state': 'opened', 'order_by': merge_order, 'sort': 'asc'},
        ))
        my_merge_request_infos = [
            mri for mri in all_merge_request_infos
            if ((mri.get('assignee', {}) or {}).get('id') == user_id) or
               (user_id in [assignee.get('id') for assignee in (mri.get('assignees', []) or [])])
        ]

        return [cls(api, merge_request_info) for merge_request_info in my_merge_request_infos]

    @property
    def project_id(self):
        return self.info['project_id']

    @property
    def iid(self):
        return self.info['iid']

    @property
    def title(self):
        return self.info['title']

    @property
    def state(self):
        return self.info['state']

    @property
    def rebase_in_progress(self):
        return self.info.get('rebase_in_progress', False)

    @property
    def merge_error(self):
        return self.info.get('merge_error')

    @property
    def assignee_ids(self):
        if 'assignees' in self.info:
            return [assignee.get('id') for assignee in (self.info['assignees'] or [])]
        return [(self.info.get('assignee', {}) or {}).get('id')]

    @property
    def author_id(self):
        return self.info['author'].get('id')

    @property
    def source_branch(self):
        return self.info['source_branch']

    @property
    def target_branch(self):
        return self.info['target_branch']

    @property
    def sha(self):
        return self.info['sha']

    @property
    def squash(self):
        return self.info.get('squash', False)  # missing means auto-squash not supported

    @property
    def source_project_id(self):
        return self.info['source_project_id']

    @property
    def target_project_id(self):
        return self.info['target_project_id']

    @property
    def work_in_progress(self):
        return self.info['work_in_progress']

    @property
    def approved_by(self):
        return self.info['approved_by']

    @property
    def web_url(self):
        return self.info['web_url']

    def refetch_info(self):
        self._info = self._api.call(GET('/projects/{0.project_id}/merge_requests/{0.iid}'.format(self)))

    def comment(self, message):
        if self._api.version().release >= (9, 2, 2):
            notes_url = '/projects/{0.project_id}/merge_requests/{0.iid}/notes'.format(self)
        else:
            # GitLab botched the v4 api before 9.2.2
            notes_url = '/projects/{0.project_id}/merge_requests/{0.id}/notes'.format(self)

        return self._api.call(POST(notes_url, {'body': message}))

    def rebase(self):
        self.refetch_info()

        if not self.rebase_in_progress:
            self._api.call(PUT(
                '/projects/{0.project_id}/merge_requests/{0.iid}/rebase'.format(self),
            ))
        else:
            # We wanted to rebase and someone just happened to press the button for us!
            log.info('A rebase was already in progress on the merge request!')

        max_attempts = 30
        wait_between_attempts_in_secs = 1

        for _ in range(max_attempts):
            self.refetch_info()
            if not self.rebase_in_progress:
                if self.merge_error:
                    raise MergeRequestRebaseFailed(self.merge_error)
                return

            time.sleep(wait_between_attempts_in_secs)

        raise TimeoutError('Waiting for merge request to be rebased by GitLab')

    def accept(self, remove_branch=False, sha=None):
        return self._api.call(PUT(
            '/projects/{0.project_id}/merge_requests/{0.iid}/merge'.format(self),
            dict(
                should_remove_source_branch=remove_branch,
                sha=sha or self.sha,  # if provided, ensures what is merged is what we want (or fails)
            ),
        ))

    def close(self):
        return self._api.call(PUT(
            '/projects/{0.project_id}/merge_requests/{0.iid}'.format(self),
            {'state_event': 'close'},
        ))

    def assign_to(self, user_id):
        return self._api.call(PUT(
            '/projects/{0.project_id}/merge_requests/{0.iid}'.format(self),
            {'assignee_id': user_id},
        ))

    def unassign(self):
        return self.assign_to(0)

    def fetch_approvals(self):
        # 'id' needed for for GitLab 9.2.2 hack (see Approvals.refetch_info())
        info = {'id': self.id, 'iid': self.iid, 'project_id': self.project_id}
        approvals = Approvals(self.api, info)
        approvals.refetch_info()
        return approvals

    def fetch_commits(self):
        return self._api.call(GET('/projects/{0.project_id}/merge_requests/{0.iid}/commits'.format(self)))


class MergeRequestRebaseFailed(Exception):
    pass
