declare namespace API {
  type AppVO = {
    id?: string | null
    appName?: string | null
    owner?: string | null
    /** 属主用户名 */
    ownerName?: string | null
    dbName?: string | null
    createTime?: string | null
    updateTime?: string | null
  }

  type AppMemberVO = {
    appId?: string | null
    userId?: string | null
    userName?: string | null
    userAccount?: string | null
    createTime?: string | null
  }

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
    appId: string
    message: string
    sessionId?: string
    requestId?: string
  }

  type DeleteRequest = {
    /** Id */
    id: string
  }

  type downloadAppCodeApiAppDownloadAppIdGetParams = {
    app_id: string
  }

  type getAppApiAppAppIdGetParams = {
    app_id: string
  }

  type getAppVoApiAppGetVoGetParams = {
    id: string
  }

  type listAppMembersApiAppMemberListAppIdGetParams = {
    app_id: string
  }

  type getAppVoByAdminApiAppAdminGetVoGetParams = {
    id: string
  }

  type getUserByIdApiUserGetGetParams = {
    id: string
  }

  type getUserVoByIdApiUserGetVoGetParams = {
    id: string
  }

  type HTTPValidationError = {
    /** Detail */
    detail?: ValidationError[]
  }

  type LoginUserVO = {
    /** Id */
    id?: string | null
    /** Useraccount */
    userAccount?: string | null
    /** Username */
    userName?: string | null
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

  type UserAddRequest = {
    /** Username（必填，不再默认"无名"） */
    userName: string
    /** User Account */
    user_account: string
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
    id?: string | null
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
    /** Username（必填） */
    userName: string
  }

  type UserUpdateRequest = {
    /** Id */
    id: string
    /** Username */
    userName?: string | null
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
