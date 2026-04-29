declare namespace API {
  type BaseResponseBool_ = {
    /** Code */
    code: number
    /** Data */
    data?: boolean | null
    /** Message */
    message: string
  }

  type BaseResponseLoginUserVO_ = {
    /** Code */
    code: number
    data?: LoginUserVO | null
    /** Message */
    message: string
  }

  type BaseResponsePageDataUserVO_ = {
    /** Code */
    code: number
    data?: PageDataUserVO_ | null
    /** Message */
    message: string
  }

  type BaseResponseStr_ = {
    /** Code */
    code: number
    /** Data */
    data?: string | null
    /** Message */
    message: string
  }

  type BaseResponseUserRawVO_ = {
    /** Code */
    code: number
    data?: UserRawVO | null
    /** Message */
    message: string
  }

  type BaseResponseUserVO_ = {
    /** Code */
    code: number
    data?: UserVO | null
    /** Message */
    message: string
  }

  type chatToGenCodeGetApiAppChatGenCodeGetParams = {
    appId: number
    message: string
    sessionId?: string
    requestId?: string
  }

  type deleteAppApiAppDeleteParams = {
    appId: number
  }

  type DeleteRequest = {
    /** Id */
    id: number
  }

  type downloadAppCodeApiAppDownloadAppIdGetParams = {
    app_id: number
  }

  type getAppApiAppAppIdGetParams = {
    app_id: number
  }

  type getAppVoApiAppGetVoGetParams = {
    id: number
  }

  type getAppVoByAdminApiAppAdminGetVoGetParams = {
    id: number
  }

  type getUserByIdApiUserGetGetParams = {
    id: number
  }

  type getUserVoByIdApiUserGetVoGetParams = {
    id: number
  }

  type HTTPValidationError = {
    /** Detail */
    detail?: ValidationError[]
  }

  type listAppChatHistoryApiChatHistoryAppAppIdGetParams = {
    app_id: number
    pageSize?: number
    lastCreateTime?: string | null
  }

  type LoginUserVO = {
    /** Id */
    id?: string | null
    /** Useraccount */
    userAccount?: string | null
    /** Username */
    userName?: string | null
    /** Useravatar */
    userAvatar?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
    /** Createtime */
    createTime?: string | null
    /** Updatetime */
    updateTime?: string | null
  }

  type PageDataUserVO_ = {
    /** Records */
    records?: UserVO[]
    /** Pagenumber */
    pageNumber: number
    /** Pagesize */
    pageSize: number
    /** Totalpage */
    totalPage: number
    /** Totalrow */
    totalRow: number
    /** Optimizecountquery */
    optimizeCountQuery?: boolean
  }

  type serveStaticResourceApiAppStaticDeployKeyGetParams = {
    deploy_key: string
    resource_path?: string
  }

  type serveStaticResourceApiAppStaticDeployKeyResourcePathGetParams = {
    deploy_key: string
    resource_path: string
  }

  type UserAddRequest = {
    /** Username */
    userName?: string | null
    /** User Account */
    user_account: string
    /** Useravatar */
    userAvatar?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
  }

  type UserLoginRequest = {
    /** Useraccount */
    userAccount: string
    /** Userpassword */
    userPassword: string
  }

  type UserQueryRequest = {
    /** Pagenum */
    pageNum?: number
    /** Pagesize */
    pageSize?: number
    /** Sortfield */
    sortField?: string | null
    /** Sortorder */
    sortOrder?: string | null
    /** Id */
    id?: number | null
    /** Username */
    userName?: string | null
    /** Useraccount */
    userAccount?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
  }

  type UserRawVO = {
    /** Id */
    id?: string | null
    /** Useraccount */
    userAccount?: string | null
    /** Userpassword */
    userPassword?: string | null
    /** Username */
    userName?: string | null
    /** Useravatar */
    userAvatar?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
    /** Userstatus */
    userStatus?: string | null
    /** Tokenquota */
    tokenQuota?: number | null
    /** Usedtokens */
    usedTokens?: number | null
    /** Edittime */
    editTime?: string | null
    /** Createtime */
    createTime?: string | null
    /** Updatetime */
    updateTime?: string | null
    /** Isdelete */
    isDelete?: number | null
  }

  type UserRegisterRequest = {
    /** Useraccount */
    userAccount: string
    /** Userpassword */
    userPassword: string
    /** Checkpassword */
    checkPassword: string
    /** Username */
    userName?: string | null
  }

  type UserUpdateRequest = {
    /** Id */
    id: number
    /** Username */
    userName?: string | null
    /** Useravatar */
    userAvatar?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
  }

  type UserVO = {
    /** Id */
    id?: string | null
    /** Useraccount */
    userAccount?: string | null
    /** Username */
    userName?: string | null
    /** Useravatar */
    userAvatar?: string | null
    /** Userprofile */
    userProfile?: string | null
    /** Userrole */
    userRole?: string | null
    /** Createtime */
    createTime?: string | null
  }

  type ValidationError = {
    /** Location */
    loc: (string | number)[]
    /** Message */
    msg: string
    /** Error Type */
    type: string
    /** Input */
    input?: any
    /** Context */
    ctx?: Record<string, any>
  }
}
