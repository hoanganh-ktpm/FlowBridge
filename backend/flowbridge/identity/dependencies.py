# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# HTTP/session boundary, shared by core routes and future resource receivers.
import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from flowbridge.identity.authentication import (
    VerifiedIdentity,
    authenticated_identity,
    verify_access_token,
)
from flowbridge.identity.directory import KeycloakDirectory
from flowbridge.identity.repository import IdentityRepository
from flowbridge.identity.service import IdentityService, PrincipalContext
from flowbridge.shared.errors import CoreError, ErrorCode


async def identity_service(request: Request) -> AsyncIterator[IdentityService]:
    resources = request.app.state.resources
    if resources is None or resources.sessions is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        async with asyncio.timeout(request.app.state.settings.database_command_timeout_seconds):
            async with resources.sessions() as session, session.begin():
                yield IdentityService(
                    request.app.state.settings,
                    IdentityRepository(session),
                    KeycloakDirectory(resources.http, request.app.state.settings),
                )
    except TimeoutError:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
    except (DBAPIError, PoolTimeoutError):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None


Service = Annotated[IdentityService, Depends(identity_service)]
Identity = Annotated[VerifiedIdentity, Depends(authenticated_identity)]


async def get_principal(identity: Identity, service: Service) -> PrincipalContext:
    return await service.resolve(identity, kind="human", permission="identity:read")


async def get_service_principal(
    service: IdentityService,
    identity: VerifiedIdentity,
    permission: str,
) -> PrincipalContext:
    return await service.resolve(identity, kind="service", permission=permission)


async def verify_user_context(
    request: Request,
    service: IdentityService,
    actor: PrincipalContext,
    permission: str,
) -> PrincipalContext:
    # IAM-issued user access token: provider verifies signature/expiry/audience via introspection.
    # Never accept caller-authored JSON, a service token as user, or an invalid header fallback.
    actor.require("context:delegate")
    actor.require(permission)
    tokens = request.headers.getlist("X-User-Context")
    if (
        actor.account_type != "service"
        or len(tokens) != 1
        or not tokens[0].strip()
        or request.headers.getlist("X-Analysis-Job-Id")
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    identity = await verify_access_token(request, tokens[0])
    user = await service.resolve(identity, kind="human", permission=permission)
    if (
        user.principal.tenant_id != actor.principal.tenant_id
        or user.application_id != actor.application_id
    ):
        raise CoreError(ErrorCode.FORBIDDEN)
    return PrincipalContext(
        user.principal.model_copy(
            update={
                "permissions": sorted(
                    set(user.principal.permissions) & set(actor.principal.permissions)
                ),
            }
        ),
        user.application_id,
        "human",
        user.client_id,
    )
