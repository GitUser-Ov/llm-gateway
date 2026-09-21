from typing import Annotated,Iterator

from fastapi import Depends

from app.core.config import Settings,get_settings

SettingsDep=Annotated[Settings,Depends(get_settings)]

class FakeResource:
    def __init__(self)->None:
        self.opened=True

    def close(self)->None:
        self.opened=False

def get_fake_resource()->Iterator[FakeResource]:
    res=FakeResource()
    try:
        yield res
    finally:
        res.close()

FakeResourceDep=Annotated[FakeResource,Depends(get_fake_resource)]

class CurrentUserLite:
    def __init__(self,name:str)->None:
        self.name=name

def get_current_user_lite(settings:SettingsDep)->CurrentUserLite:
    name="anonymous" if settings.debug else "unknown"
    return CurrentUserLite(name=name)

CurrentUserLiteDep=Annotated[CurrentUserLite,Depends(get_current_user_lite)]
